"""The window: a world map on the left, everything you can do with a pin on the right."""

import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog

from . import targets
from .globe import WorldMap
from .presets import PRESETS

BG = "#04060d"
PANEL = "#0b1120"
LINE = "#1b2b47"
TEXT = "#dbe6ff"
MUTED = "#7d8bad"
CYAN = "#00e5ff"
MAGENTA = "#ff2fd0"
OK = "#9dff4f"
BAD = "#ff5b6e"
MONO = ("Menlo", 11)
MONO_SM = ("Menlo", 10)
SANS = ("Helvetica Neue", 12)


class NeonButton(tk.Frame):
    """macOS Tk ignores bg on native buttons, so this is a label pretending to be one."""

    def __init__(self, master, text, command, accent=CYAN, small=False):
        super().__init__(master, bg=PANEL, highlightbackground=LINE,
                         highlightthickness=1, bd=0)
        self.command = command
        self.accent = accent
        self.label = tk.Label(self, text=text, bg=PANEL, fg=TEXT,
                              font=("Menlo", 10 if small else 11),
                              padx=8, pady=4 if small else 6, cursor="pointinghand")
        self.label.pack(fill="both", expand=True)
        for widget in (self, self.label):
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
            widget.bind("<Button-1>", self._click)

    def _enter(self, _):
        self.label.configure(fg=self.accent)
        self.configure(highlightbackground=self.accent)

    def _leave(self, _):
        self.label.configure(fg=TEXT)
        self.configure(highlightbackground=LINE)

    def _click(self, _):
        self.command()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GPS Hopper")
        self.geometry("1180x720")
        self.minsize(880, 560)
        self.configure(bg=BG)

        self.lat, self.lon = 89.9999, 0.0
        self.jobs = queue.Queue()
        self.log_queue = queue.Queue()
        self.wander_on = tk.BooleanVar(value=False)
        self.wander_busy = False
        # Every Chrome call runs on the single worker thread, so this needs no lock.
        self.chrome = targets.ChromeSession()
        self.push_var = tk.StringVar(value="nothing pushed yet")

        self._build()
        self._set_coords(self.lat, self.lon, fly=False)

        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()
        self.after(80, self._drain_log)
        self.after(200, lambda: (self.map.fit_world(),
                                 self.map.set_marker(self.lat, self.lon)))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── layout ───────────────────────────────────────────────────────────
    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(header, text="GPS HOPPER", bg=BG, fg=CYAN,
                 font=("Menlo", 17, "bold")).pack(side="left")
        tk.Label(header, text="  pick a spot on Earth, send it to a device",
                 bg=BG, fg=MUTED, font=SANS).pack(side="left")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        map_wrap = tk.Frame(left, bg=BG, highlightbackground=LINE, highlightthickness=1)
        map_wrap.pack(fill="both", expand=True)
        self.map = WorldMap(map_wrap, on_pick=self._on_pick, bg=BG)
        self.map.pack(fill="both", expand=True)

        bar = tk.Frame(left, bg=BG)
        bar.pack(fill="x", pady=(8, 0))
        NeonButton(bar, "−", lambda: self.map.zoom_by(1 / 1.4), small=True).pack(side="left")
        NeonButton(bar, "+", lambda: self.map.zoom_by(1.4), small=True).pack(side="left", padx=4)
        NeonButton(bar, "fit world", self.map.fit_world, small=True).pack(side="left")
        NeonButton(bar, "zoom to pin",
                   lambda: self.map.center_on(self.lat, self.lon, 24), small=True
                   ).pack(side="left", padx=4)
        self.hover_label = tk.Label(bar, textvariable=self.map.hover_text, bg=BG,
                                    fg=MUTED, font=MONO_SM)
        self.hover_label.pack(side="right")

        right = tk.Frame(body, bg=BG, width=330)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)
        self._build_panel(right)

    def _section(self, parent, title):
        tk.Label(parent, text=title, bg=BG, fg=MUTED,
                 font=("Menlo", 9, "bold")).pack(anchor="w", pady=(12, 4))
        box = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=PANEL)
        inner.pack(fill="x", padx=10, pady=8)
        return inner

    def _build_panel(self, parent):
        # coordinates
        coords = self._section(parent, "COORDINATES")
        entry_row = tk.Frame(coords, bg=PANEL)
        entry_row.pack(fill="x")
        self.coord_var = tk.StringVar()
        entry = tk.Entry(entry_row, textvariable=self.coord_var, bg="#0e1626", fg=TEXT,
                         insertbackground=CYAN, font=MONO, relief="flat",
                         highlightbackground=LINE, highlightthickness=1)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda _e: self._apply_typed())
        NeonButton(entry_row, "go", self._apply_typed, small=True).pack(side="left", padx=(6, 0))
        tk.Label(coords, text="paste \"lat, lon\" or click the map",
                 bg=PANEL, fg=MUTED, font=MONO_SM).pack(anchor="w", pady=(4, 0))

        acc_row = tk.Frame(coords, bg=PANEL)
        acc_row.pack(fill="x", pady=(6, 0))
        tk.Label(acc_row, text="accuracy", bg=PANEL, fg=MUTED, font=MONO_SM).pack(side="left")
        self.acc_var = tk.StringVar(value="20")
        tk.Spinbox(acc_row, from_=1, to=5000, increment=5, width=7,
                   textvariable=self.acc_var, font=MONO_SM, relief="flat",
                   bg="#0e1626", fg=TEXT, buttonbackground=PANEL,
                   highlightbackground=LINE, highlightthickness=1).pack(side="left", padx=6)
        tk.Label(acc_row, text="metres", bg=PANEL, fg=MUTED, font=MONO_SM).pack(side="left")

        # presets
        presets = self._section(parent, "JUMP TO")
        listbox = tk.Listbox(presets, height=7, bg="#0e1626", fg=TEXT,
                             selectbackground=CYAN, selectforeground="#04060d",
                             font=MONO_SM, relief="flat", highlightthickness=0,
                             activestyle="none")
        for name, _lat, _lon in PRESETS:
            listbox.insert("end", "  " + name)
        listbox.pack(fill="x")
        listbox.bind("<<ListboxSelect>>", self._on_preset)
        self.preset_list = listbox

        # send
        send = self._section(parent, "SEND IT")
        NeonButton(send, "▸  hop Chrome to here", self._hop_chrome).pack(fill="x", pady=2)
        NeonButton(send, "◇  launch debug Chrome", self._launch_chrome, small=True).pack(fill="x", pady=2)
        NeonButton(send, "✕  clear Chrome override", self._clear_chrome,
                   accent=BAD, small=True).pack(fill="x", pady=2)
        NeonButton(send, "⬇  save GPX for Xcode…", self._save_gpx, small=True).pack(fill="x", pady=2)
        NeonButton(send, "🤖  send to Android emulator", self._hop_android, small=True).pack(fill="x", pady=2)
        NeonButton(send, "⧉  copy coordinates", self._copy, small=True).pack(fill="x", pady=2)

        tk.Label(send, textvariable=self.push_var, bg=PANEL, fg=MUTED,
                 font=MONO_SM, anchor="w", justify="left").pack(fill="x", pady=(8, 0))

        wander = tk.Frame(send, bg=PANEL)
        wander.pack(fill="x", pady=(6, 0))
        tk.Checkbutton(wander, text="wander (\u00b18 m jitter, too small to see on a map)",
                       variable=self.wander_on, command=self._toggle_wander,
                       bg=PANEL, fg=TEXT, selectcolor=PANEL, activebackground=PANEL,
                       activeforeground=CYAN, font=MONO_SM, highlightthickness=0,
                       bd=0).pack(anchor="w")

        # log
        tk.Label(parent, text="LOG", bg=BG, fg=MUTED,
                 font=("Menlo", 9, "bold")).pack(anchor="w", pady=(12, 4))
        log_box = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        log_box.pack(fill="both", expand=True)
        self.log = tk.Text(log_box, bg=PANEL, fg=TEXT, font=MONO_SM, relief="flat",
                           height=8, wrap="word", highlightthickness=0,
                           padx=8, pady=6, state="disabled")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("bad", foreground=BAD)
        self.log.tag_configure("dim", foreground=MUTED)
        self._log("ready — click the map or pick a preset", "dim")

    # ── state ────────────────────────────────────────────────────────────
    def _set_coords(self, lat, lon, fly=False):
        self.lat, self.lon = lat, lon
        self.coord_var.set("%.5f, %.5f" % (lat, lon))
        self.map.set_marker(lat, lon)
        if fly:
            self.map.center_on(lat, lon)

    def _on_pick(self, lat, lon):
        self._set_coords(lat, lon)
        self.preset_list.selection_clear(0, "end")

    def _on_preset(self, _event):
        sel = self.preset_list.curselection()
        if not sel:
            return
        name, lat, lon = PRESETS[sel[0]]
        self._set_coords(lat, lon, fly=True)
        self._log("aimed at %s" % name, "dim")

    def _apply_typed(self):
        raw = self.coord_var.get().replace(",", " ").split()
        try:
            lat, lon = float(raw[0]), float(raw[1])
        except (ValueError, IndexError):
            self._log("could not read that as \"lat, lon\"", "bad")
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._log("out of range — lat is ±90, lon is ±180", "bad")
            return
        self._set_coords(lat, lon, fly=True)

    def _accuracy(self):
        try:
            return max(1.0, float(self.acc_var.get()))
        except ValueError:
            return 20.0

    # ── actions (each hands the slow part to the worker thread) ──────────
    def _hop_chrome(self):
        lat, lon, acc = self.lat, self.lon, self._accuracy()

        def task():
            count = self.chrome.set_location(lat, lon, acc)
            self._pushed(lat, lon, count)
            return ("Chrome hopped to %.4f, %.4f (%d tab%s) — keep this app open"
                    % (lat, lon, count, "" if count == 1 else "s"))

        self._run(task, "pushing to Chrome…")

    def _launch_chrome(self):
        def task():
            targets.launch_chrome()
            return "debug Chrome is up on port 9222 — open a site, then hop"

        self._run(task, "starting Chrome with a throwaway profile…")

    def _clear_chrome(self):
        def task():
            return "override cleared on %d tab(s)" % self.chrome.clear()

        self._run(task, "clearing…")

    def _hop_android(self):
        lat, lon = self.lat, self.lon

        def task():
            return "sent to %s" % targets.set_android_location(lat, lon)

        self._run(task, "talking to adb…")

    def _save_gpx(self):
        path = filedialog.asksaveasfilename(
            title="Save GPX", defaultextension=".gpx",
            initialfile="hop.gpx", filetypes=[("GPX track", "*.gpx")])
        if not path:
            return
        try:
            targets.write_gpx(path, self.lat, self.lon, "GPS Hopper",
                              points=120, spread_m=4.0)
        except OSError as exc:
            self._log("could not write file: %s" % exc, "bad")
            return
        self._log("saved %s — Xcode ▸ Devices ▸ Simulate Location" % path, "ok")

    def _copy(self):
        text = "%.6f, %.6f" % (self.lat, self.lon)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log("copied %s" % text, "ok")

    def _toggle_wander(self):
        if self.wander_on.get():
            self._log("wander on — \u00b18 m every 3s; the readout above will tick",
                      "dim")
            self._wander_tick()
        else:
            self._log("wander off", "dim")

    def _wander_tick(self):
        if not self.wander_on.get():
            return
        if not self.wander_busy:
            self.wander_busy = True
            lat, lon = targets.jitter(self.lat, self.lon, 8.0)
            acc = self._accuracy()

            def task():
                count = self.chrome.set_location(lat, lon, acc)
                self._pushed(lat, lon, count)
                return None                     # quiet: this runs every few seconds

            self._run(task, None, done=self._wander_done)
        self.after(3000, self._wander_tick)

    def _pushed(self, lat, lon, count):
        """Show what Chrome was last told. Called from the worker thread.

        Without this, wander looks identical to a broken app: the position is
        updating every three seconds and absolutely nothing on screen says so.
        """
        stamp = time.strftime("%H:%M:%S")
        text = "Chrome has %.5f, %.5f  ·  %d tab%s  ·  %s" % (
            lat, lon, count, "" if count == 1 else "s", stamp)
        self.log_queue.put((lambda: self.push_var.set(text), "callback"))

    def _wander_done(self):
        self.wander_busy = False

    def _on_close(self):
        # Chrome drops the override the moment these sockets close, so say so
        # rather than letting the prank quietly end when the window does.
        self.wander_on.set(False)
        try:
            self.chrome.close()
        except Exception:                                  # noqa: BLE001
            pass
        self.destroy()

    # ── worker plumbing ──────────────────────────────────────────────────
    def _run(self, task, pending_msg, done=None):
        if pending_msg:
            self._log(pending_msg, "dim")
        self.jobs.put((task, done))

    def _work(self):
        while True:
            task, done = self.jobs.get()
            try:
                message = task()
                if message:
                    self.log_queue.put((message, "ok"))
            except targets.TargetError as exc:
                self.log_queue.put((str(exc), "bad"))
            except Exception as exc:                      # noqa: BLE001
                self.log_queue.put(("%s: %s" % (type(exc).__name__, exc), "bad"))
            finally:
                if done:
                    self.log_queue.put((done, "callback"))

    def _drain_log(self):
        while True:
            try:
                message, kind = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "callback":
                message()
            else:
                self._log(message, kind)
        self.after(80, self._drain_log)

    def _log(self, message, kind="dim"):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n", kind)
        self.log.see("end")
        self.log.configure(state="disabled")


def main():
    App().mainloop()
