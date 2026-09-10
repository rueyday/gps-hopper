"""A clickable world map on a plain Tk canvas.

Equirectangular projection, drawn from baked-in coastlines. No tiles, no
network, no Pillow -- which matters because the Tk that ships with macOS is
8.5 and cannot even decode a PNG.
"""

import tkinter as tk

from .worlddata import land_rings

OCEAN = "#04060d"
LAND = "#0d2033"
COAST = "#00b8cc"
GRID = "#152238"
EQUATOR = "#1e3350"
MARK = "#00e5ff"
MARK_GLOW = "#ff2fd0"
TEXT = "#dbe6ff"

MIN_SCALE = 1.4          # pixels per degree at full zoom-out
MAX_SCALE = 260.0


def _grid_step(scale):
    """Graticule spacing that keeps roughly one line every 90-ish pixels."""
    for step in (30, 15, 10, 5, 2, 1):
        if step * scale >= 70:
            return step
    return 0.5


class WorldMap(tk.Frame):
    def __init__(self, master, on_pick=None, **kw):
        super().__init__(master, **kw)
        self.on_pick = on_pick
        self.scale = MIN_SCALE
        self.offx = 0.0                       # world pixel at the left edge
        self.offy = 0.0                       # world pixel at the top edge
        self.marker = None                    # (lat, lon)
        self._rings = list(land_rings())
        self._drag = None
        self._moved = 0

        self.canvas = tk.Canvas(self, bg=OCEAN, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<MouseWheel>", self._on_wheel)          # macOS / Windows
        self.canvas.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.15))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 1 / 1.15))
        self.hover_text = tk.StringVar(value="")

    # ── projection ───────────────────────────────────────────────────────
    @property
    def world_w(self):
        return 360.0 * self.scale

    @property
    def world_h(self):
        return 180.0 * self.scale

    def to_screen(self, lat, lon):
        return ((lon + 180.0) * self.scale - self.offx,
                (90.0 - lat) * self.scale - self.offy)

    def to_latlon(self, x, y):
        lon = (x + self.offx) / self.scale - 180.0
        lat = 90.0 - (y + self.offy) / self.scale
        lon = (lon + 180.0) % 360.0 - 180.0
        return max(-90.0, min(90.0, lat)), lon

    # ── viewport housekeeping ────────────────────────────────────────────
    def _clamp_offsets(self):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.offx = (max(0.0, min(self.offx, self.world_w - cw))
                     if self.world_w > cw else (self.world_w - cw) / 2.0)
        self.offy = (max(0.0, min(self.offy, self.world_h - ch))
                     if self.world_h > ch else (self.world_h - ch) / 2.0)

    def _on_resize(self, _event=None):
        cw = max(1, self.canvas.winfo_width())
        # Never zoom out past "the whole world fits", it only wastes screen.
        fit = cw / 360.0
        if self.scale < fit:
            self.scale = fit
        self._clamp_offsets()
        self.redraw()

    def fit_world(self):
        cw = max(1, self.canvas.winfo_width())
        self.scale = max(MIN_SCALE, cw / 360.0)
        self._clamp_offsets()
        self.redraw()

    def center_on(self, lat, lon, scale=None):
        if scale:
            self.scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.offx = (lon + 180.0) * self.scale - cw / 2.0
        self.offy = (90.0 - lat) * self.scale - ch / 2.0
        self._clamp_offsets()
        self.redraw()

    def _zoom_at(self, x, y, factor):
        before = self.to_latlon(x, y)
        cw = max(1, self.canvas.winfo_width())
        low = max(MIN_SCALE, cw / 360.0)
        self.scale = max(low, min(MAX_SCALE, self.scale * factor))
        # Keep whatever was under the cursor under the cursor.
        self.offx = (before[1] + 180.0) * self.scale - x
        self.offy = (90.0 - before[0]) * self.scale - y
        self._clamp_offsets()
        self.redraw()

    def zoom_by(self, factor):
        self._zoom_at(self.canvas.winfo_width() / 2,
                      self.canvas.winfo_height() / 2, factor)

    def _on_wheel(self, event):
        step = event.delta
        self._zoom_at(event.x, event.y, 1.1 if step > 0 else 1 / 1.1)

    # ── pointer ──────────────────────────────────────────────────────────
    def _on_press(self, event):
        self._drag = (event.x, event.y, self.offx, self.offy)
        self._moved = 0

    def _on_drag(self, event):
        if not self._drag:
            return
        x0, y0, ox, oy = self._drag
        dx, dy = event.x - x0, event.y - y0
        self._moved = max(self._moved, abs(dx) + abs(dy))
        self.offx, self.offy = ox - dx, oy - dy
        self._clamp_offsets()
        self.redraw()

    def _on_release(self, event):
        drag = self._drag
        self._drag = None
        if drag and self._moved < 4:          # a click, not the end of a pan
            lat, lon = self.to_latlon(event.x, event.y)
            self.set_marker(lat, lon)
            if self.on_pick:
                self.on_pick(lat, lon)

    def _on_hover(self, event):
        lat, lon = self.to_latlon(event.x, event.y)
        self.hover_text.set("%.4f, %.4f" % (lat, lon))

    def set_marker(self, lat, lon):
        self.marker = (lat, lon)
        self._draw_marker()

    # ── drawing ──────────────────────────────────────────────────────────
    def redraw(self):
        c = self.canvas
        c.delete("all")
        cw = max(1, c.winfo_width())
        ch = max(1, c.winfo_height())

        left, top = -self.offx, -self.offy
        c.create_rectangle(left, top, left + self.world_w, top + self.world_h,
                           fill=OCEAN, outline=GRID)

        step = _grid_step(self.scale)
        lon = -180.0
        while lon <= 180.0:
            x = (lon + 180.0) * self.scale - self.offx
            if -20 <= x <= cw + 20:
                c.create_line(x, top, x, top + self.world_h,
                              fill=EQUATOR if lon == 0 else GRID)
            lon += step
        lat = -90.0
        while lat <= 90.0:
            y = (90.0 - lat) * self.scale - self.offy
            if -20 <= y <= ch + 20:
                c.create_line(left, y, left + self.world_w, y,
                              fill=EQUATOR if lat == 0 else GRID)
            lat += step

        for ring in self._rings:
            pts = []
            for lon_v, lat_v in ring:
                x, y = self.to_screen(lat_v, lon_v)
                pts.extend((x, y))
            if len(pts) >= 6:
                c.create_polygon(pts, fill=LAND, outline=COAST, width=1)

        self._draw_marker()

    def _draw_marker(self):
        c = self.canvas
        c.delete("marker")
        if not self.marker:
            return
        lat, lon = self.marker
        x, y = self.to_screen(lat, lon)
        for radius, colour in ((16, MARK_GLOW), (9, MARK)):
            c.create_oval(x - radius, y - radius, x + radius, y + radius,
                          outline=colour, width=1, tags="marker")
        c.create_line(x - 22, y, x + 22, y, fill=MARK, tags="marker")
        c.create_line(x, y - 22, x, y + 22, fill=MARK, tags="marker")
        label = "%.4f, %.4f" % (lat, lon)
        c.create_text(x + 26, y - 16, text=label, fill=TEXT, anchor="w",
                      font=("Menlo", 10), tags="marker")
