"""Ways of getting a coordinate out of this app and into something that reads GPS.

Everything here is standard library only -- including a very small WebSocket
client, because talking to Chrome's DevTools protocol is the one target that
needs one and pulling in a dependency for ~70 lines felt like a bad trade.
"""

import base64
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.request import urlopen


class TargetError(Exception):
    """Something went wrong reaching a target; the message is shown to the user."""


# ── GPX (Xcode / iOS, and most mapping software) ─────────────────────────────

def write_gpx(path, lat, lon, name="Hopped location", points=1, spread_m=6.0,
              interval_s=1.0):
    """Write a GPX file at *path*.

    A single point gives Xcode a fixed position. More than one gives it a track
    it will walk through, which reads as a phone sitting still with normal GPS
    jitter rather than a coordinate nailed to three decimal places forever.
    """
    if points <= 1:
        body = ('  <wpt lat="%.6f" lon="%.6f">\n    <name>%s</name>\n  </wpt>\n'
                % (lat, lon, _xml_escape(name)))
    else:
        start = datetime.now(timezone.utc)
        rows = []
        for i in range(points):
            plat, plon = jitter(lat, lon, spread_m)
            stamp = (start + timedelta(seconds=i * interval_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows.append('      <trkpt lat="%.6f" lon="%.6f"><time>%s</time></trkpt>'
                        % (plat, plon, stamp))
        body = ("  <trk>\n    <name>%s</name>\n    <trkseg>\n%s\n    </trkseg>\n  </trk>\n"
                % (_xml_escape(name), "\n".join(rows)))

    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<gpx version="1.1" creator="gps-hopper" '
           'xmlns="http://www.topografix.com/GPX/1/1">\n%s</gpx>\n' % body)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def jitter(lat, lon, spread_m):
    """Nudge a coordinate by up to roughly *spread_m* metres in a random direction."""
    if spread_m <= 0:
        return lat, lon
    dlat = random.uniform(-spread_m, spread_m) / 111_320.0
    scale = max(0.01, abs(_cos_deg(lat)))
    dlon = random.uniform(-spread_m, spread_m) / (111_320.0 * scale)
    return _clamp(lat + dlat, -90, 90), _wrap_lon(lon + dlon)


def _cos_deg(deg):
    import math
    return math.cos(math.radians(deg))


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _wrap_lon(lon):
    return (lon + 180.0) % 360.0 - 180.0


# ── a minimal WebSocket client, just enough for DevTools ─────────────────────

class _WebSocket:
    def __init__(self, url, timeout=6.0):
        parts = urlparse(url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
        )
        self.sock.sendall(handshake.encode())

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise TargetError("Chrome closed the connection during handshake")
            header += chunk
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise TargetError("Chrome refused the WebSocket upgrade")
        self._buf = header.split(b"\r\n\r\n", 1)[1]
        self._msg_id = 0
        self.origin = None

    def next_id(self):
        self._msg_id += 1
        return self._msg_id

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise TargetError("Chrome closed the connection")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text):
        payload = text.encode()
        head = bytearray([0x81])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n <= 0xFFFF:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def recv(self):
        """Return the next text message, quietly answering pings along the way."""
        while True:
            b0, b1 = self._read(2)
            opcode = b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
            if opcode == 0x9:            # ping -> pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0x8:
                raise TargetError("Chrome closed the connection")
            if opcode in (0x1, 0x0):
                return data.decode("utf-8", "replace")
            # binary or anything else: not something DevTools sends us, skip it

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ── Chrome DevTools Protocol ─────────────────────────────────────────────────

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_chrome():
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return shutil.which("google-chrome") or shutil.which("chromium")


def launch_chrome(port=9222, profile_dir=None):
    """Start a separate Chrome instance with debugging on.

    It gets its own profile directory on purpose: Chrome ignores
    --remote-debugging-port when it would attach to your everyday profile, and
    a throwaway profile keeps this well away from your real logins.
    """
    exe = find_chrome()
    if not exe:
        raise TargetError("No Chrome/Chromium/Brave/Edge found in /Applications")
    profile_dir = profile_dir or os.path.join(
        os.path.expanduser("~"), ".cache", "gps-hopper", "chrome-profile")
    os.makedirs(profile_dir, exist_ok=True)
    subprocess.Popen(
        [exe, "--remote-debugging-port=%d" % port,
         "--user-data-dir=%s" % profile_dir, "--no-first-run",
         "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):                       # up to ~8s for the port to open
        time.sleep(0.2)
        try:
            list_pages(port)
            return exe
        except TargetError:
            continue
    raise TargetError("Chrome started but never opened the debugging port")


def list_pages(port=9222):
    try:
        with urlopen("http://127.0.0.1:%d/json" % port, timeout=3) as resp:
            targets = json.load(resp)
    except Exception as exc:
        raise TargetError("No debuggable Chrome on port %d (%s)" % (port, exc))
    return [t for t in targets
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


class ChromeSession:
    """Holds DevTools connections open for as long as the override should last.

    This is the whole reason the class exists. Both Emulation.setGeolocationOverride
    and Browser.setPermission are scoped to the DevTools client that set them --
    disconnect and Chrome quietly restores the real location. So connections to
    every open tab stay open until the user clears the override or quits.
    """

    def __init__(self, port=9222):
        self.port = port
        self._conns = {}                    # target id -> _WebSocket
        self._last = None                   # (lat, lon, accuracy) for new tabs

    def _sync_tabs(self):
        """Open connections to tabs we haven't seen, drop ones that went away."""
        pages = list_pages(self.port)
        alive = set()
        for page in pages:
            tid = page.get("id")
            alive.add(tid)
            if tid in self._conns:
                continue
            try:
                ws = _WebSocket(page["webSocketDebuggerUrl"])
            except (OSError, TargetError):
                continue                     # tab may have closed mid-scan
            ws.origin = _origin_of(page.get("url", ""))
            self._conns[tid] = ws
        for tid in list(self._conns):
            if tid not in alive:
                self._conns.pop(tid).close()
        return pages

    def set_location(self, lat, lon, accuracy=20.0):
        self._sync_tabs()
        if not self._conns:
            raise TargetError("Chrome is running but has no open tabs")
        self._last = (lat, lon, accuracy)
        done, errors = 0, []
        for tid, ws in list(self._conns.items()):
            try:
                if getattr(ws, "origin", None):
                    _grant_geolocation(ws, ws.origin)
                _call(ws, ws.next_id(), "Emulation.setGeolocationOverride",
                      {"latitude": lat, "longitude": lon, "accuracy": accuracy})
                done += 1
            except (OSError, TargetError) as exc:
                errors.append(str(exc))
                self._conns.pop(tid).close()
        if not done:
            raise TargetError("; ".join(errors) or "Chrome accepted nothing")
        return done

    def reapply(self):
        """Re-push the last position, picking up any tabs opened since."""
        if not self._last:
            return 0
        return self.set_location(*self._last)

    def clear(self):
        count = 0
        for tid, ws in list(self._conns.items()):
            try:
                _call(ws, ws.next_id(), "Emulation.clearGeolocationOverride", {})
                count += 1
            except (OSError, TargetError):
                pass
            self._conns.pop(tid).close()
        self._last = None
        return count

    def close(self):
        for ws in self._conns.values():
            ws.close()
        self._conns.clear()

    @property
    def tab_count(self):
        return len(self._conns)


def _grant_geolocation(ws, origin):
    """Pre-approve geolocation so the page doesn't stall on a permission prompt.

    Browser.setPermission is the one that actually sticks; grantPermissions
    reports success and leaves the state on "prompt", so it is only a fallback
    for older builds that lack setPermission. Failing here is not fatal -- the
    user can still click Allow in the page.
    """
    try:
        _call(ws, ws.next_id(), "Browser.setPermission",
              {"origin": origin, "permission": {"name": "geolocation"},
               "setting": "granted"})
        return True
    except (OSError, TargetError):
        pass
    try:
        _call(ws, ws.next_id(), "Browser.grantPermissions",
              {"origin": origin, "permissions": ["geolocation"]})
        return True
    except (OSError, TargetError):
        return False


def _call(ws, msg_id, method, params):
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
    deadline = time.time() + 6
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("id") != msg_id:
            continue                       # an unrelated DevTools event
        if "error" in msg:
            raise TargetError(msg["error"].get("message", method + " failed"))
        return msg.get("result", {})
    raise TargetError(method + " timed out")


def _origin_of(url):
    parts = urlparse(url)
    if parts.scheme in ("http", "https") and parts.netloc:
        return "%s://%s" % (parts.scheme, parts.netloc)
    return None


# ── Android via adb ──────────────────────────────────────────────────────────

def adb_path():
    found = shutil.which("adb")
    if found:
        return found
    guess = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
    return guess if os.path.exists(guess) else None


def adb_devices():
    exe = adb_path()
    if not exe:
        raise TargetError("adb not found (install Android platform-tools)")
    out = subprocess.run([exe, "devices"], capture_output=True, text=True, timeout=10)
    devices = []
    for line in out.stdout.splitlines()[1:]:
        bits = line.split()
        if len(bits) >= 2 and bits[1] == "device":
            devices.append(bits[0])
    return devices


def set_android_location(lat, lon, serial=None):
    """Works on emulators, which accept `geo fix` directly.

    Physical Android phones have no equivalent -- they need a mock-location app
    selected in Developer options -- so this reports that rather than pretending.
    """
    exe = adb_path()
    if not exe:
        raise TargetError("adb not found (install Android platform-tools)")
    devices = adb_devices()
    if not devices:
        raise TargetError("No Android device or emulator attached")
    serial = serial or devices[0]
    if not serial.startswith("emulator-"):
        raise TargetError(
            "%s is a physical device -- adb cannot set its location. Use a "
            "mock-location app plus Developer options > Select mock location app."
            % serial)
    result = subprocess.run([exe, "-s", serial, "emu", "geo", "fix",
                             "%.6f" % lon, "%.6f" % lat],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise TargetError(result.stderr.strip() or "adb emu geo fix failed")
    return serial
