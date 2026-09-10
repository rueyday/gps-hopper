# GPS Hopper

Pick a spot on a world map, send it to something that reads GPS.

![no dependencies](https://img.shields.io/badge/dependencies-none-00e5ff) ![python](https://img.shields.io/badge/python-3.8%2B%20with%20Tk%208.6-8b6bff)

A small Tk desktop app. Click anywhere on Earth — or paste coordinates, or pick
a preset — and push that position into Chrome, an Android emulator, or a GPX
file for Xcode's location simulator.

Pure standard library. No `pip install`, no tile server, no network calls except
the ones you ask for: the coastlines are baked into the repo and the DevTools
WebSocket client is about seventy lines of `socket`.

```
./run.sh
```

## What it can drive

| Target | What happens | Catch |
| --- | --- | --- |
| **Chrome** | Overrides `navigator.geolocation` in every open tab | Only while GPS Hopper stays open — see below |
| **Xcode / iPhone** | Writes a GPX file you load with *Simulate Location* | Position holds only while the phone is tethered to Xcode |
| **Android emulator** | `adb emu geo fix` | Emulators only; physical phones need a mock-location app |
| **Clipboard** | Plain `lat, lon` | — |

### The Chrome override is tied to this app staying open

Chrome scopes `Emulation.setGeolocationOverride` to the DevTools client that set
it. Connect, set the position, disconnect, and Chrome silently restores your
real location. So GPS Hopper holds its WebSocket connections open for as long as
the override should last. **Quit the app and the spoof ends.** That is Chrome's
design, not a bug here.

Two more things that trip people up:

- **Geolocation needs a secure context.** It works on `https://` pages and on
  `localhost`, and is refused on `http://` and `file://` — the error Chrome
  reports is the slightly misleading *"Only secure origins are allowed"*.
- **Use the debug Chrome button.** Chrome ignores `--remote-debugging-port` when
  it would attach to your everyday profile, so the app launches a separate
  instance with a throwaway profile in `~/.cache/gps-hopper`. Your real logins
  and cookies are untouched.

## Using it

1. `./run.sh`
2. **launch debug Chrome**, then open whatever site you want fooled in that window.
3. Click the map, or type `lat, lon`, or pick a preset from *jump to*.
4. **hop Chrome to here.**

Scroll to zoom, drag to pan, `+` / `−` / *fit world* / *zoom to pin* along the bottom.

**wander** nudges the position by a few metres every three seconds. A coordinate
frozen to six decimal places for an hour looks like exactly what it is; real GPS
never sits perfectly still.

**accuracy** is the radius, in metres, that the page is told to expect. Small
values read as a clean GPS lock, large ones as a rough Wi-Fi guess.

## Requirements

Python 3.8+ with Tk **8.6**. `run.sh` finds one for you. The system
`/usr/bin/python3` on macOS is stuck on Tk 8.5 and will not start on recent
macOS; conda, python.org and `brew install python-tk` all work.

## Layout

```
gpshopper/
  app.py         the window, and the worker thread that does the slow parts
  globe.py       the clickable map: equirectangular projection on a Tk canvas
  targets.py     GPX writer, adb, and a minimal WebSocket + DevTools client
  presets.py     places worth jumping to
  worlddata.py   Natural Earth 110m coastlines, generated, ~5k points
```

## Fair warning

This spoofs *your own* devices, which is what location simulation is built for.
Apps with fraud checks — banking, ride-share, dating — often detect mock
locations and may lock the account. Keep it to the group chat.

Coastline data: [Natural Earth](https://www.naturalearthdata.com/), public domain.
