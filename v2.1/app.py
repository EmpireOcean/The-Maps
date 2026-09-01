from __future__ import annotations

import json
import math
import os
import sys
import ctypes
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from ctypes import wintypes

import pystray
import requests
from PIL import Image, ImageTk

import islepilot
import localtelemetry


RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_ROOT = (
    Path(os.environ.get("APPDATA", Path.home())) / "The-Maps"
    if getattr(sys, "frozen", False)
    else RESOURCE_ROOT
)
MAPS_DIR = RESOURCE_ROOT / "maps"
CONFIG_PATH = DATA_ROOT / "config.json"
APP_ICON_ICO = RESOURCE_ROOT / "assets" / "the_maps.ico"
APP_ICON_PNG = RESOURCE_ROOT / "assets" / "the_maps.png"
YOUTUBE_URL = "https://www.youtube.com/@GlobalDailyHighlights"
DISCORD_URL = "https://discord.gg/XpkRPpDhPU"
APP_VERSION = "2.1"

# v1.3 and v2.1 ship together in one combined GitHub Release — bump this
# to the new tag *at the same time* you actually publish that release on
# GitHub, not before: this is compared with != against the API's current
# "latest" tag (not a version-ordering check), so a local build whose tag
# doesn't match yet — ahead or behind — always trips the "update available"
# prompt.
RELEASE_TAG = "v5"
GITHUB_RELEASE_API = "https://api.github.com/repos/EmpireOcean/The-Maps/releases/latest"
GITHUB_RELEASE_PAGE = "https://github.com/EmpireOcean/The-Maps/releases/latest"
UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000

VK_TAB = 0x09
DEFAULT_TOGGLE_MAP_VK = 0x4D  # 'M'
VK_N = 0x4E  # 'N' — toggles the whole IslePilot HUD (minimap + quest panel) on/off; not user-configurable
MIN_ZOOM = 1.0
MAX_ZOOM = 6.0
ZOOM_STEP = 1.15
HQ_REDRAW_DELAY_MS = 120

# Zone overlays are pre-rendered PNGs (hand-traced by the user directly over
# the Gateway basemap, same pixel dimensions as map.webp) composited straight
# onto the map image at render time — pixel-accurate, no vectorization.
# (data key, chip label, image filename in the profile's map folder, chip color)
ZONE_LAYERS: tuple[tuple[str, str, str, str], ...] = (
    ("migrations", "Migration", "zone_migration.png", "#ff9800"),
    ("patrol_zones", "Patrol", "zone_patrol.png", "#ab47bc"),
)


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class MapProfile:
    profile_id: str
    name: str
    image_path: Path | None
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    swap_axes: bool = False
    invert_x: bool = False
    invert_y: bool = False
    heading_offset_deg: float = 0.0
    zone_image_paths: dict[str, Path] = field(default_factory=dict)

    def to_normalized(self, position: Position) -> tuple[float, float]:
        if self.swap_axes:
            nx = (position.y - self.min_y) / (self.max_y - self.min_y)
            ny = (position.x - self.min_x) / (self.max_x - self.min_x)
        else:
            nx = (position.x - self.min_x) / (self.max_x - self.min_x)
            ny = (position.y - self.min_y) / (self.max_y - self.min_y)
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny
        return nx, ny

    def transform_yaw(self, yaw_degrees: float) -> float:
        """World yaw to a screen-space compass heading in degrees, clockwise
        from up — ready to feed straight into the canvas arrow rotation.
        Yaw is negated because Unreal's rotation sense turned out to be the
        mirror image of ours (confirmed live: turning right in-game turned
        the arrow left)."""
        yaw_rad = math.radians(-yaw_degrees)
        world_dx, world_dy = math.cos(yaw_rad), math.sin(yaw_rad)
        if self.swap_axes:
            screen_dx, screen_dy = world_dy, world_dx
        else:
            screen_dx, screen_dy = world_dx, world_dy
        if self.invert_x:
            screen_dx = -screen_dx
        if self.invert_y:
            screen_dy = -screen_dy
        heading = math.degrees(math.atan2(screen_dx, -screen_dy)) + self.heading_offset_deg
        return heading % 360.0


def load_profiles() -> list[MapProfile]:
    profiles: list[MapProfile] = []
    for manifest in sorted(MAPS_DIR.glob("*/map.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            bounds = data["world_bounds"]
            image = manifest.parent / data["image"] if data.get("image") else None
            zone_image_paths = {}
            for key, _label, filename, _color in ZONE_LAYERS:
                zone_file = manifest.parent / filename
                if zone_file.exists():
                    zone_image_paths[key] = zone_file
            profiles.append(
                MapProfile(
                    profile_id=data["id"],
                    name=data["name"],
                    image_path=image if image and image.exists() else None,
                    min_x=float(bounds["min_x"]),
                    max_x=float(bounds["max_x"]),
                    min_y=float(bounds["min_y"]),
                    max_y=float(bounds["max_y"]),
                    swap_axes=bool(data.get("swap_axes", False)),
                    invert_x=bool(data.get("invert_x", False)),
                    invert_y=bool(data.get("invert_y", False)),
                    heading_offset_deg=float(data.get("heading_offset_deg", 0.0)),
                    zone_image_paths=zone_image_paths,
                )
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if not profiles:
        raise RuntimeError("Không tìm thấy map profile hợp lệ trong thư mục maps.")
    return profiles


def _heading_polygon_points(cx: float, cy: float, heading_deg: float, size: float) -> list[float]:
    """Kite-shaped arrow, tip pointing toward heading_deg (0 = up, clockwise)."""
    theta = math.radians(heading_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    def rotate(local_x: float, local_y: float) -> tuple[float, float]:
        return (
            cx + cos_t * local_x - sin_t * local_y,
            cy + sin_t * local_x + cos_t * local_y,
        )

    tip = rotate(0.0, -size)
    left = rotate(-size * 0.62, size * 0.75)
    back = rotate(0.0, size * 0.28)
    right = rotate(size * 0.62, size * 0.75)
    return [*tip, *left, *back, *right]


def _draw_heading_polygon(canvas: tk.Canvas, cx: float, cy: float, heading_deg: float, size: float, color: str) -> None:
    canvas.create_polygon(
        _heading_polygon_points(cx, cy, heading_deg, size),
        fill=color, outline="white", width=2, joinstyle="round",
    )


def _ensure_taskbar_button(window: tk.Toplevel) -> None:
    """Give a Toplevel its own taskbar entry so its native minimize button
    actually works. Without this, a Toplevel owned by an overrideredirect
    window (our hidden map root) inherits WS_EX_TOOLWINDOW on Windows,
    which hides it from the taskbar — minimize then has nowhere to send it,
    so it silently does nothing."""
    try:
        window.update_idletasks()
        user32 = ctypes.windll.user32
        GA_ROOT = 2
        GWL_EXSTYLE = -20
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_TOOLWINDOW = 0x00000080
        hwnd = user32.GetAncestor(window.winfo_id(), GA_ROOT)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        window.withdraw()
        window.deiconify()
    except OSError:
        pass


_GA_ROOT = 2
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE_FLAG = 0x0010
_SWP_FRAMECHANGED = 0x0020
_LWA_ALPHA = 0x00000002


def _hud_hwnd(window: tk.Toplevel) -> int | None:
    try:
        window.update_idletasks()
        return ctypes.windll.user32.GetAncestor(window.winfo_id(), _GA_ROOT)
    except OSError:
        return None


def _apply_exstyle(hwnd: int, style: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
    # SetWindowLongW alone doesn't reliably repaint the window under its new
    # style — without forcing SWP_FRAMECHANGED, Windows can keep compositing
    # from the old (pre-style-change) surface, which is what was showing up
    # as black panels, a squished-looking minimap, and quest text that
    # never redrew after collapsing/expanding.
    user32.SetWindowPos(
        hwnd, None, 0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE_FLAG | _SWP_FRAMECHANGED,
    )


def _set_noactivate(window: tk.Toplevel) -> None:
    """Stop `window` from ever becoming the foreground/active window, even
    when it's clicked — a plain overrideredirect Toplevel steals foreground
    activation on click, which used to make the game lose focus for a tick
    and the HUD's own foreground-only visibility check hide it right back."""
    hwnd = _hud_hwnd(window)
    if hwnd is None:
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        _apply_exstyle(hwnd, style | _WS_EX_NOACTIVATE)
    except OSError:
        pass


def _make_click_through(window: tk.Toplevel) -> None:
    """Make every pixel of `window` invisible to mouse input — clicks (and
    everything else) fall straight through to whatever is beneath it on
    screen, i.e. the game. Used for HUD body panels, which are meant to be
    read, not clicked; only the small drag-handle window stays interactive.

    WS_EX_TRANSPARENT on its own reports HTTRANSPARENT for every point in
    the window, which is documented as what makes clicks fall through —
    but verified against a real click (SendInput + a listener window
    underneath, not just visual inspection) that turned out to be false
    for a plain GDI-painted window: the OS still delivered the click to
    this window instead of passing it on. Only reports getting through
    once WS_EX_LAYERED is set too, so this window's hit-testing is fully
    handed off to DWM's compositor rather than GDI's. LAYERED was removed
    at one point over a suspicion it caused the black-panel rendering bug
    — that bug was actually the missing SWP_FRAMECHANGED (see
    _apply_exstyle); with that in place, LAYERED here is safe and, per
    the same click test, required."""
    hwnd = _hud_hwnd(window)
    if hwnd is None:
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        _apply_exstyle(hwnd, style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE)
        # A layered window needs its layered attributes set at least once
        # or it can render blank/black — full opacity, no color key.
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, _LWA_ALPHA)
    except OSError:
        pass


_LWA_COLORKEY = 0x00000001


def _make_colorkey_click_through(window: tk.Toplevel, colorkey: str) -> None:
    """Like _make_click_through, but pixels matching `colorkey` are made
    visually transparent (see straight through to whatever's behind the
    window) instead of the whole window being uniformly opaque — used for
    the fake cursor, which is a small arrow glyph on an otherwise empty
    window. Every pixel, drawn or not, is still click-through (WS_EX_
    TRANSPARENT), same as the rest of the HUD; LWA_COLORKEY only controls
    what's painted, not hit-testing."""
    hwnd = _hud_hwnd(window)
    if hwnd is None:
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        _apply_exstyle(hwnd, style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE)
        r16, g16, b16 = window.winfo_rgb(colorkey)
        colorref = (r16 >> 8) | ((g16 >> 8) << 8) | ((b16 >> 8) << 16)
        user32.SetLayeredWindowAttributes(hwnd, colorref, 0, _LWA_COLORKEY)
    except OSError:
        pass


class _Point(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class _CursorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", _Point),
    )


_CURSOR_SHOWING = 0x00000001


def _get_cursor_state() -> tuple[bool, int, int] | None:
    """(is_showing, x, y) for the real system cursor, or None on failure.
    Click routing and cursor-shape routing share the same hit-test, so
    once a HUD panel is genuinely click-through, whichever window is
    *under* it also decides whether a cursor shows there at all — see
    _FakeCursor."""
    info = _CursorInfo()
    info.cbSize = ctypes.sizeof(_CursorInfo)
    if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(info)):
        return None
    return (bool(info.flags & _CURSOR_SHOWING), info.ptScreenPos.x, info.ptScreenPos.y)


_TIMER_RESOLUTION_MS = 1


def _raise_timer_resolution() -> None:
    """Ask Windows for ~1ms timer/wait granularity for this process, instead
    of the usual default of ~15.6ms (1000/64).

    _poll_fake_cursor asks Tk's root.after() to fire every FAKE_CURSOR_
    TRACK_MS — but that request is only ever as accurate as the underlying
    OS wait primitive Tcl uses under the hood, which by default quantizes to
    the system timer tick (~15.6ms). Requesting an 8ms (or 4ms) callback
    without this call doesn't make it fire at 8ms/4ms — it silently rounds
    up to the next ~15.6ms tick, and jitters between 1x and 2x that as the
    scheduler's phase drifts. That's a plausible, well-documented cause of
    the fake cursor visibly stepping/stalling during fast, continuous
    movement (e.g. a fast circle) even though nothing in this app itself is
    slow: the *timer* granularity is the bottleneck, not the work being
    done in each tick.

    One important caveat found while looking into this: on Windows 11, a
    process's own timeBeginPeriod request can be reverted by the OS while
    that process is occluded/minimized/invisible (this was tightened up in
    Windows 10 2004+, which also made the effect per-process rather than
    system-wide — see the "Great Rule Change" writeup by Bruce Dawson).
    The-Maps' HUD windows are always genuinely on-screen and composited
    (just click-through and non-activating, never literally hidden or
    covered), so this shouldn't trigger that reversion in practice, but
    it's worth knowing about if the effect ever seems to stop helping after
    the window has been alt-tabbed around for a while.

    Trade-off: this measurably increases how often the CPU wakes from idle
    for the whole process's lifetime, which costs a little extra power —
    acceptable here since this only runs while actively playing a game,
    which is already keeping the GPU/CPU busy."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(_TIMER_RESOLUTION_MS)
    except OSError:
        pass


def _restore_timer_resolution() -> None:
    try:
        ctypes.windll.winmm.timeEndPeriod(_TIMER_RESOLUTION_MS)
    except OSError:
        pass


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return {}


def _save_config_update(**updates) -> None:
    """Merge-update top-level keys in config.json instead of overwriting it —
    map profile, HUD positions and minimap zoom are all saved independently."""
    config = _load_config()
    config.update(updates)
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError:
        pass


def _update_hud_config(**hud_updates) -> None:
    config = _load_config()
    hud = config.get("hud")
    if not isinstance(hud, dict):
        hud = {}
    hud.update(hud_updates)
    _save_config_update(hud=hud)


DRAG_CLICK_THRESHOLD_PX = 4
HANDLE_HEIGHT = 8


def _create_handle_window(
    root: tk.Tk, width: int, pos: tuple[int, int], on_move, on_drag_end, on_click=None,
) -> tk.Toplevel:
    """A slim draggable strip, own top-level window so it can stay
    interactive while the rest of the HUD panel (a separate window, see
    _make_click_through) is click-through to the game underneath.

    A press+release with no real movement counts as a plain click (fires
    on_click) rather than a drag — deliberately NOT a double-click: the
    handle is only ~8px tall, and real double-clicks drift a pixel or two
    between the two presses, which is enough for Tk to miss recognizing
    them as one Double-Button-1 on a target this small. A single click
    has no such precision problem.
    """
    window = tk.Toplevel(root)
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    window.configure(bg=HUD_BG, cursor="fleur")
    window.geometry(f"{width}x{HANDLE_HEIGHT}+{pos[0]}+{pos[1]}")
    _set_noactivate(window)

    grip = tk.Label(window, text="⋯", fg="#607d8b", bg=HUD_BG, font=("Segoe UI", 6, "bold"))
    grip.pack(expand=True, fill="both")

    drag_state: dict[str, object] = {"start": None, "moved": False}

    def on_press(event) -> None:
        drag_state["start"] = (event.x_root, event.y_root, window.winfo_x(), window.winfo_y())
        drag_state["moved"] = False

    def on_motion(event) -> None:
        start = drag_state["start"]
        if start is None:
            return
        start_x_root, start_y_root, start_win_x, start_win_y = start
        dx = event.x_root - start_x_root
        dy = event.y_root - start_y_root
        if abs(dx) > DRAG_CLICK_THRESHOLD_PX or abs(dy) > DRAG_CLICK_THRESHOLD_PX:
            drag_state["moved"] = True
        new_x, new_y = start_win_x + dx, start_win_y + dy
        window.geometry(f"+{new_x}+{new_y}")
        on_move(new_x, new_y)

    def on_release(_event) -> None:
        if drag_state["start"] is None:
            return
        moved = drag_state["moved"]
        drag_state["start"] = None
        if moved:
            on_drag_end(window.winfo_x(), window.winfo_y())
        elif on_click is not None:
            on_click()

    # Bind on both the window and the label sitting on top of it — a click
    # landing on the label (the only visible part) must work too, not just
    # clicks on the bare window background around it.
    for widget in (window, grip):
        widget.bind("<ButtonPress-1>", on_press)
        widget.bind("<B1-Motion>", on_motion)
        widget.bind("<ButtonRelease-1>", on_release)
    return window


def _vk_display_name(vk: int) -> str:
    try:
        user32 = ctypes.windll.user32
        scan_code = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
        buf = ctypes.create_unicode_buffer(64)
        length = user32.GetKeyNameTextW(scan_code << 16, buf, 64)
        if length > 0:
            return buf.value
    except OSError:
        pass
    return f"VK 0x{vk:02X}"


def _pin_width(parent: tk.Widget, width: int) -> None:
    """A zero-height spacer packed first, so `parent`'s pack-negotiated
    width is always exactly `width` — matching the drag-handle window sized
    to that same width above it. Without this, the body window's width was
    whatever its widest actual child happened to want (e.g. a canvas plus
    its highlight border coming out 2px over, or wrapped labels that never
    reach the panel's full width), which visibly didn't line up with the
    handle strip."""
    spacer = tk.Frame(parent, bg=HUD_BG, width=width, height=0)
    spacer.pack(side="top")
    spacer.pack_propagate(False)


def _format_stat(value: float) -> str:
    # A hatchling's max pool can be a small fraction (e.g. 0.4) that would
    # otherwise truncate to a confusing "0" — keep one decimal below 10.
    if abs(value) < 10:
        return f"{value:.1f}"
    return str(int(round(value)))


HUD_MARGIN = 12
MINI_MAP_SIZE = 220  # overall panel width — matches the handle strip above it
MINI_MAP_BORDER = 2
# The map image itself is inset by the border on every side, rather than
# the border being added on top of MINI_MAP_SIZE — that's what was making
# the canvas 2*MINI_MAP_BORDER wider than MINI_MAP_SIZE (a Tk highlight
# border draws outside the configured width/height), which was the actual
# cause of the handle/body width mismatch reported earlier.
MINI_MAP_IMAGE_SIZE = MINI_MAP_SIZE - 2 * MINI_MAP_BORDER
MINI_MAP_CROP_FRACTION = 0.16
MINI_MAP_MIN_CROP = 0.04
MINI_MAP_MAX_CROP = 0.4
MINI_MAP_ZOOM_STEP = 1.15
VITAL_BAR_HEIGHT = 14
VITAL_BAR_PADDING = 8  # 4px on each side of the bar canvas
VITAL_BAR_MIN_WIDTH = 40
QUEST_PANEL_WIDTH = 260
HUD_BG = "#10191d"
VITAL_BAR_SPECS = (
    ("health", "Máu", "#e74c3c"),
    ("stamina", "Stamina", "#f1c40f"),
    ("thirst", "Nước", "#3498db"),
    ("hunger", "Food", "#e67e22"),
)


class MiniMapPanel:
    """Player-centered mini-map + vitals bars, pinned to the top-left corner.

    Two top-level windows stacked to look like one panel: a small opaque
    drag handle (`self.handle`, click-to-move) sitting right above a fully
    click-through body (`self.body`, canvas + vitals) — see
    _create_handle_window and _make_click_through. Wheel-zoom on the canvas
    can't be a normal Tk binding since the body never receives mouse input;
    MapApp's low-level mouse hook drives zoom_by_delta() instead."""

    def __init__(self, root: tk.Tk):
        raw_hud = _load_config().get("hud")
        hud_config = raw_hud if isinstance(raw_hud, dict) else {}
        pos = hud_config.get("minimap_pos")
        self._crop_fraction = float(hud_config.get("minimap_crop", MINI_MAP_CROP_FRACTION))
        self._crop_fraction = min(MINI_MAP_MAX_CROP, max(MINI_MAP_MIN_CROP, self._crop_fraction))

        if isinstance(pos, list) and len(pos) == 2:
            handle_x, handle_y = int(pos[0]), int(pos[1])
        else:
            handle_x, handle_y = HUD_MARGIN, HUD_MARGIN

        self.handle = _create_handle_window(
            root, MINI_MAP_SIZE, (handle_x, handle_y),
            on_move=self._follow_handle,
            on_drag_end=lambda x, y: _update_hud_config(minimap_pos=[x, y]),
        )

        self.body = tk.Toplevel(root)
        self.body.overrideredirect(True)
        self.body.attributes("-topmost", True)
        self.body.configure(bg=HUD_BG)
        self.body.geometry(f"+{handle_x}+{handle_y + HANDLE_HEIGHT}")

        # A Tk highlight border draws *outside* the given width/height, so
        # the canvas is sized to MINI_MAP_IMAGE_SIZE (already inset by the
        # border) rather than MINI_MAP_SIZE — total on-screen size still
        # comes out to exactly MINI_MAP_SIZE, matching the handle strip.
        self.canvas = tk.Canvas(
            self.body, width=MINI_MAP_IMAGE_SIZE, height=MINI_MAP_IMAGE_SIZE,
            background="#1b262c",
            highlightthickness=MINI_MAP_BORDER, highlightbackground="#37474f",
        )
        self.canvas.pack()

        bars_frame = tk.Frame(self.body, bg=HUD_BG)
        bars_frame.pack(fill="x", pady=(6, 4))

        self._bar_canvases: dict[str, tk.Canvas] = {}
        self._bar_labels: dict[str, tk.Label] = {}
        for key, label, _color in VITAL_BAR_SPECS:
            row = tk.Frame(bars_frame, bg=HUD_BG)
            row.pack(fill="x", pady=1)
            name_label = tk.Label(
                row, text=label, fg="#cfd8dc", bg=HUD_BG,
                font=("Segoe UI", 8), width=7, anchor="w",
            )
            name_label.pack(side="left")
            bar_canvas = tk.Canvas(row, height=VITAL_BAR_HEIGHT, background="#26343a", highlightthickness=0)
            bar_canvas.pack(side="left", padx=(4, 4))
            # No fixed width here — a long value like "1234/5678" needs to
            # stay fully readable, so update_vitals() shrinks the bar
            # canvas to make room for it instead of letting the text grow
            # past its slot (which plain tk.Label doesn't clip — it just
            # draws over whatever is next to it) or growing this row past
            # MINI_MAP_SIZE (which self.body's pack_propagate(False) cap
            # forbids anyway).
            value_label = tk.Label(
                row, text="--", fg="#90a4ae", bg=HUD_BG, font=("Segoe UI", 8), anchor="e",
            )
            value_label.pack(side="left")
            self._bar_canvases[key] = bar_canvas
            self._bar_labels[key] = value_label

        # All 4 name labels share the same font/width config, so one
        # post-layout measurement covers every row's budget in
        # update_vitals(). Must come after packing — reqwidth is only
        # accurate once Tk has actually computed geometry.
        self.body.update_idletasks()
        self._vital_name_label_width = name_label.winfo_reqwidth()

        self._photo: ImageTk.PhotoImage | None = None
        self._last_map_args: tuple | None = None
        # Hard cap, not just a floor: pack_propagate(False) means no child —
        # now or later, however wide its content gets — can ever grow this
        # window past MINI_MAP_SIZE again. Measure the natural height first
        # since, unlike QuestPanel, this panel's content never changes
        # shape at runtime, so freezing it here is safe.
        self.body.update_idletasks()
        self.body.geometry(f"{MINI_MAP_SIZE}x{self.body.winfo_reqheight()}")
        self.body.pack_propagate(False)
        _make_click_through(self.body)
        self._visible = True
        self.hide()

    def _follow_handle(self, x: int, y: int) -> None:
        self.body.geometry(f"+{x}+{y + HANDLE_HEIGHT}")

    def show(self) -> None:
        # State transition only — _poll_hud_visibility calls this every
        # 300ms while the HUD is meant to be visible, and deiconify() is a
        # real show transition, not a no-op when already shown; calling it
        # unconditionally kept re-asserting this window's topmost stacking
        # above the fake cursor, fighting its own lift() every tick.
        if not self._visible:
            self.handle.deiconify()
            self.body.deiconify()
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            self.handle.withdraw()
            self.body.withdraw()
            self._visible = False

    def canvas_screen_rect(self) -> tuple[int, int, int, int] | None:
        """Screen-space bounds of the minimap canvas, for MapApp's low-level
        mouse hook to test wheel events against — read from a background
        thread, so it must stay a plain tuple, never call into Tk."""
        if not self.body.winfo_viewable():
            return None
        x = self.canvas.winfo_rootx()
        y = self.canvas.winfo_rooty()
        return (x, y, x + self.canvas.winfo_width(), y + self.canvas.winfo_height())

    def body_screen_rect(self) -> tuple[int, int, int, int] | None:
        """Screen-space bounds of the whole click-through body (map +
        vitals), for MapApp's fake-cursor tracking."""
        if not self.body.winfo_viewable():
            return None
        x = self.body.winfo_rootx()
        y = self.body.winfo_rooty()
        return (x, y, x + self.body.winfo_width(), y + self.body.winfo_height())

    def zoom_by_delta(self, delta: int) -> None:
        factor = MINI_MAP_ZOOM_STEP if delta > 0 else (1.0 / MINI_MAP_ZOOM_STEP)
        new_fraction = min(MINI_MAP_MAX_CROP, max(MINI_MAP_MIN_CROP, self._crop_fraction / factor))
        if new_fraction == self._crop_fraction:
            return
        self._crop_fraction = new_fraction
        _update_hud_config(minimap_crop=self._crop_fraction)
        if self._last_map_args is not None:
            self.update_map(*self._last_map_args)

    def update_map(
        self, source_image, profile: MapProfile, x: float, y: float, heading_deg: float,
        zone_images: tuple["Image.Image", ...] = (),
    ) -> None:
        self._last_map_args = (source_image, profile, x, y, heading_deg, zone_images)
        self.canvas.delete("all")
        if source_image is None:
            return
        nx, ny = profile.to_normalized(Position(x, y, 0.0))
        width, height = source_image.size
        frac = self._crop_fraction
        left = min(max(nx - frac / 2, 0.0), 1.0 - frac)
        top = min(max(ny - frac / 2, 0.0), 1.0 - frac)
        crop_box = (
            int(left * width), int(top * height),
            int((left + frac) * width), int((top + frac) * height),
        )
        cropped = source_image.crop(crop_box).convert("RGBA")
        for zone_image in zone_images:
            cropped.alpha_composite(zone_image.crop(crop_box))
        resized = cropped.resize((MINI_MAP_IMAGE_SIZE, MINI_MAP_IMAGE_SIZE), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        # Normally the player is centered. Near the source-image edges the
        # crop is clamped, so compute the marker's true position inside the
        # crop instead of blindly drawing it at the center.
        marker_x = (nx - left) / frac * MINI_MAP_IMAGE_SIZE
        marker_y = (ny - top) / frac * MINI_MAP_IMAGE_SIZE
        marker_x = max(0.0, min(float(MINI_MAP_IMAGE_SIZE), marker_x))
        marker_y = max(0.0, min(float(MINI_MAP_IMAGE_SIZE), marker_y))
        _draw_heading_polygon(self.canvas, marker_x, marker_y, heading_deg, 11, "#ff5b45")

    def update_vitals(self, status: "islepilot.IslePilotStatus") -> None:
        values = {
            "health": (status.health, status.max_health),
            "stamina": (status.stamina, status.max_stamina),
            "thirst": (status.thirst, status.max_thirst),
            "hunger": (status.hunger, status.max_hunger),
        }

        # Pass 1: set every value's text and measure it. The number must
        # stay fully readable, so the bar width is driven by whichever row
        # needs the *most* room — then that one shared width is applied to
        # every row, so all four bars end at the same edge instead of each
        # independently claiming whatever its own value left over (which
        # made short values like "553/553" draw a longer bar than a row
        # with a longer value like "319/1411" right above it).
        max_value_width = 0
        for key, _label, _color in VITAL_BAR_SPECS:
            current, maximum = values[key]
            text = f"{_format_stat(current)}/{_format_stat(maximum)}" if current is not None and maximum else "--"
            value_label = self._bar_labels[key]
            value_label.configure(text=text)
            value_label.update_idletasks()
            max_value_width = max(max_value_width, value_label.winfo_reqwidth())

        width = max(
            VITAL_BAR_MIN_WIDTH,
            MINI_MAP_SIZE - self._vital_name_label_width - VITAL_BAR_PADDING - max_value_width,
        )
        height = VITAL_BAR_HEIGHT

        # Pass 2: draw every bar at that one shared width.
        for key, _label, color in VITAL_BAR_SPECS:
            canvas = self._bar_canvases[key]
            canvas.configure(width=width)
            canvas.delete("all")
            canvas.create_rectangle(0, 0, width, height, fill="#26343a", outline="")
            current, maximum = values[key]
            if current is not None and maximum:
                fraction = max(0.0, min(1.0, current / maximum))
                if fraction > 0:
                    canvas.create_rectangle(0, 0, width * fraction, height, fill=color, outline="")

    def destroy(self) -> None:
        self.handle.destroy()
        self.body.destroy()


PRIME_GROWTH_CUTOFF_PERCENT = 75.0


class QuestPanel:
    """Prime quest checklist (up to 10 items), pinned to the top-right corner.

    Auto-collapses to a single summary line once the outcome is already
    decided — either you've done enough quests (qualified) or you've passed
    the growth cutoff without enough done (Prime Elder is no longer
    reachable this life) — so the full 10-row list only takes up space while
    it's still actually in play.
    """

    def __init__(self, root: tk.Tk):
        raw_hud = _load_config().get("hud")
        hud_config = raw_hud if isinstance(raw_hud, dict) else {}
        pos = hud_config.get("quest_pos")

        if isinstance(pos, list) and len(pos) == 2:
            handle_x, handle_y = int(pos[0]), int(pos[1])
        else:
            screen_width = root.winfo_screenwidth()
            handle_x = screen_width - QUEST_PANEL_WIDTH - HUD_MARGIN
            handle_y = HUD_MARGIN

        self._manually_collapsed = False
        self._text_hidden = False
        self._last_status: "islepilot.IslePilotStatus | None" = None

        self.handle = _create_handle_window(
            root, QUEST_PANEL_WIDTH, (handle_x, handle_y),
            on_move=self._follow_handle,
            on_drag_end=lambda x, y: _update_hud_config(quest_pos=[x, y]),
            on_click=self._toggle_manual_collapse,
        )

        self.body = tk.Toplevel(root)
        self.body.overrideredirect(True)
        self.body.attributes("-topmost", True)
        self.body.configure(bg=HUD_BG)
        self.body.geometry(f"+{handle_x}+{handle_y + HANDLE_HEIGHT}")
        _pin_width(self.body, QUEST_PANEL_WIDTH)

        self.header_var = tk.StringVar(value="Prime quest")
        self._header_label = tk.Label(
            self.body, textvariable=self.header_var, fg="#ffd54f", bg=HUD_BG,
            font=("Segoe UI", 9, "bold"), anchor="w", wraplength=QUEST_PANEL_WIDTH - 16,
        )
        self._header_label.pack(fill="x", padx=8, pady=(6, 4))

        self._summary_label = tk.Label(
            self.body, text="", bg=HUD_BG, font=("Segoe UI", 9, "bold"),
            anchor="w", wraplength=QUEST_PANEL_WIDTH - 16, justify="left",
        )

        self._rows_frame = tk.Frame(self.body, bg=HUD_BG)
        self._rows: list[tuple[tk.Label, tk.Label]] = []
        for _ in range(10):
            row = tk.Frame(self._rows_frame, bg=HUD_BG)
            row.pack(fill="x", padx=8, pady=1)
            check = tk.Label(row, text="", fg="#607d8b", bg=HUD_BG, font=("Segoe UI", 9), width=2)
            check.pack(side="left")
            name = tk.Label(
                row, text="", fg="#cfd8dc", bg=HUD_BG, font=("Segoe UI", 9),
                anchor="w", wraplength=QUEST_PANEL_WIDTH - 40, justify="left",
            )
            name.pack(side="left", fill="x", expand=True)
            self._rows.append((check, name))
        self._rows_frame.pack(fill="x")

        _make_click_through(self.body)
        self._visible = True
        self.hide()

    def _follow_handle(self, x: int, y: int) -> None:
        self.body.geometry(f"+{x}+{y + HANDLE_HEIGHT}")

    def show(self) -> None:
        # See MiniMapPanel.show() — state transition only, not a no-op-safe
        # deiconify() called unconditionally every poll tick.
        if not self._visible:
            self.handle.deiconify()
            self.body.deiconify()
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            self.handle.withdraw()
            self.body.withdraw()
            self._visible = False

    def body_screen_rect(self) -> tuple[int, int, int, int] | None:
        """Screen-space bounds of the whole click-through body (quest
        header + rows), for MapApp's fake-cursor tracking."""
        if not self.body.winfo_viewable():
            return None
        x = self.body.winfo_rootx()
        y = self.body.winfo_rooty()
        return (x, y, x + self.body.winfo_width(), y + self.body.winfo_height())

    @staticmethod
    def _classify(status: "islepilot.IslePilotStatus") -> tuple[bool, bool]:
        """Returns (qualified, missed_cutoff) — both are terminal/permanent
        outcomes for the current life, once true they stay true."""
        qualified = status.prime_done >= status.prime_required > 0
        growth_percent = status.growth
        if growth_percent is not None and growth_percent <= 1.0:
            growth_percent *= 100.0
        missed_cutoff = (
            not qualified
            and growth_percent is not None
            and growth_percent > PRIME_GROWTH_CUTOFF_PERCENT
        )
        return qualified, missed_cutoff

    def _toggle_manual_collapse(self) -> None:
        if self._last_status is not None and any(self._classify(self._last_status)):
            # Outcome is already decided — clicking cycles the summary
            # line itself away, down to just the bare drag bar.
            self._text_hidden = not self._text_hidden
        else:
            self._manually_collapsed = not self._manually_collapsed
        if self._last_status is not None:
            self.update(self._last_status)

    def update(self, status: "islepilot.IslePilotStatus") -> None:
        self._last_status = status
        qualified, missed_cutoff = self._classify(status)

        if (qualified or missed_cutoff) and self._text_hidden:
            self._show_bar_only()
        elif qualified:
            self._show_summary(
                f"✓ Đủ điều kiện Prime Elder ({status.prime_done}/{status.prime_total})",
                "#4caf50",
            )
        elif missed_cutoff:
            missing = max(0, status.prime_required - status.prime_done)
            self._show_summary(
                f"✗ Không thể thành Prime Elder — thiếu {missing} nhiệm vụ trước "
                f"{PRIME_GROWTH_CUTOFF_PERCENT:.0f}% growth",
                "#e74c3c",
            )
        elif self._manually_collapsed:
            self._show_summary(
                f"📋 Danh sách nhiệm vụ Prime ({status.prime_done}/{status.prime_total} · cần {status.prime_required})",
                "#ffd54f",
            )
        else:
            self._show_full_list(status)

    def _show_bar_only(self) -> None:
        self._header_label.pack_forget()
        self._summary_label.pack_forget()
        self._rows_frame.pack_forget()

    def _show_summary(self, text: str, color: str) -> None:
        self._header_label.pack_forget()
        self._rows_frame.pack_forget()
        self._summary_label.configure(text=text, fg=color)
        self._summary_label.pack(fill="x", padx=8, pady=(4, 6))

    def _show_full_list(self, status: "islepilot.IslePilotStatus") -> None:
        self._summary_label.pack_forget()
        self.header_var.set(f"Prime: {status.prime_done}/{status.prime_total} (cần {status.prime_required})")
        self._header_label.pack(fill="x", padx=8, pady=(6, 4))
        for index, (check, name) in enumerate(self._rows):
            if index < len(status.quests):
                quest = status.quests[index]
                check.configure(text="✓" if quest.done else "○", fg="#4caf50" if quest.done else "#607d8b")
                name.configure(text=islepilot.translate_quest(quest.name))
            else:
                check.configure(text="")
                name.configure(text="")
        self._rows_frame.pack(fill="x")

    def destroy(self) -> None:
        self.handle.destroy()
        self.body.destroy()


_FAKE_CURSOR_SIZE = 20
_FAKE_CURSOR_COLORKEY = "#010203"  # arbitrary, just needs to never appear elsewhere


class _FakeCursor:
    """Stand-in arrow drawn wherever the real system cursor is hidden while
    hovering a click-through HUD body — see MapApp._poll_fake_cursor.

    Click routing and cursor-shape routing share the same OS hit-test, so
    once a HUD panel is genuinely click-through (see _make_click_through),
    the *game* underneath also decides whether any cursor shows there —
    same as everywhere else on screen during normal play. If the game
    hides its cursor while playing, hovering the HUD now hides it too,
    which reads as the HUD having "no cursor". This never draws while the
    real cursor is actually visible; it only fills the gap."""

    def __init__(self, root: tk.Tk):
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=_FAKE_CURSOR_COLORKEY)
        self.window.geometry(f"{_FAKE_CURSOR_SIZE}x{_FAKE_CURSOR_SIZE}+0+0")
        self.canvas = tk.Canvas(
            self.window, width=_FAKE_CURSOR_SIZE, height=_FAKE_CURSOR_SIZE,
            bg=_FAKE_CURSOR_COLORKEY, highlightthickness=0,
        )
        self.canvas.pack()
        # A plain arrow silhouette, tip at (0, 0) to match a real cursor's
        # hotspot — the window is positioned so this point sits exactly on
        # the actual cursor coordinate.
        self.canvas.create_polygon(
            0, 0, 0, 15, 4, 11, 7, 18, 9, 17, 6, 10, 11, 10,
            fill="white", outline="black", width=1,
        )
        _make_colorkey_click_through(self.window, _FAKE_CURSOR_COLORKEY)
        # Cached once: re-resolving this (winfo_id + GetAncestor) on every
        # single move would defeat the point of using SetWindowPos directly
        # instead of Tk's geometry() — the whole reason being to skip
        # Tk/Tcl round-trips on the hot path.
        self._hwnd = _hud_hwnd(self.window)
        self._visible = False
        self._last_moved_to: tuple[int, int] | None = None
        self.window.withdraw()

    def move_to(self, x: int, y: int) -> None:
        """Reposition only — no show/hide, no z-order change, no Tk
        geometry(). Called at the fast tracking rate, so this has to stay
        as cheap as a single SetWindowPos call."""
        if (x, y) == self._last_moved_to or self._hwnd is None:
            return
        self._last_moved_to = (x, y)
        ctypes.windll.user32.SetWindowPos(
            self._hwnd, None, x, y, 0, 0,
            _SWP_NOSIZE | _SWP_NOACTIVATE_FLAG | _SWP_NOZORDER,
        )

    def show(self) -> None:
        """State transition only (hidden -> visible) — callers must only
        invoke this on an actual transition, not every tick; see MapApp's
        fake-cursor state machine (_should_show_fake_cursor)."""
        if not self._visible:
            self.window.deiconify()
            self._visible = True
            self.raise_above_hud()

    def raise_above_hud(self) -> None:
        """Re-assert this window's stacking above the HUD panels. Called
        only on this window's own show() transition and by MapApp when HUD
        visibility itself transitions to visible — never on a timer or
        every tick, which is what caused the z-order fight with
        _poll_hud_visibility's periodic re-show in the first place."""
        self.window.lift()

    def hide(self) -> None:
        if self._visible:
            self.window.withdraw()
            self._visible = False
            self._last_moved_to = None

    def destroy(self) -> None:
        self.window.destroy()


class IslePilotHud:
    """Bundles the mini-map and quest panel so MapApp can treat them as one unit."""

    def __init__(self, root: tk.Tk):
        self.minimap = MiniMapPanel(root)
        self.quests = QuestPanel(root)

    def show(self, show_quests: bool = True) -> None:
        self.minimap.show()
        if show_quests:
            self.quests.show()
        else:
            self.quests.hide()

    def hide(self) -> None:
        self.minimap.hide()
        self.quests.hide()

    def update_map(
        self, source_image, profile: MapProfile, x: float, y: float, heading_deg: float,
        zone_images: tuple["Image.Image", ...] = (),
    ) -> None:
        self.minimap.update_map(source_image, profile, x, y, heading_deg, zone_images=zone_images)

    def update_vitals(self, status: "islepilot.IslePilotStatus") -> None:
        self.minimap.update_vitals(status)

    def update_quests(self, status: "islepilot.IslePilotStatus") -> None:
        self.quests.update(status)

    def destroy(self) -> None:
        self.minimap.destroy()
        self.quests.destroy()


FOREGROUND_POLL_MS = 300
# One combined GetCursorInfo call drives both the show/hide decision and
# position tracking every tick — see _poll_fake_cursor. Requesting this
# interval only actually gets honored at this granularity because main()
# calls _raise_timer_resolution() — without it, Tk's root.after() quantizes
# to Windows' default ~15.6ms timer tick regardless of what's requested
# here, which is what was making fast circular movement look like it
# stepped/stalled every so often even though nothing was "slow". 4ms
# (250 Hz) comfortably covers even a 240Hz gaming monitor's refresh rate
# with the now-real ~1ms timer granularity backing it; going faster than
# that has no visible benefit (nothing refreshes the screen that often) and
# just spends more CPU time on GetCursorInfo/SetWindowPos calls for nothing.
FAKE_CURSOR_TRACK_MS = 4
FAKE_CURSOR_REAL_VISIBLE_DEBOUNCE_SECONDS = 0.2  # a single transient GetCursorInfo sample must not hide the fake cursor
LOCAL_POSITION_FRESH_SECONDS = 2.0
# Npcap publishes at ~8-10 Hz — shifting older/previous on every sample would
# bunch the full-map trail into a near-invisible cluster around the current
# marker. Only advance the trail this often instead, so it reads the same as
# the clipboard-driven trail (spaced-out waypoints, not a solid smear).
LOCAL_TRAIL_MIN_INTERVAL_SECONDS = 1.5
# _apply_local_movement used to re-render the minimap (crop + alpha_composite
# + LANCZOS resize + a fresh ImageTk.PhotoImage) on every single local sample
# — up to 20 Hz (see _MIN_PUBLISH_INTERVAL in localtelemetry.py). All of that
# runs on the Tk main thread and holds the GIL for most of it (PIL's resize/
# composite plus the PhotoImage->Tcl marshal), which is the same thread and
# the same GIL that _poll_fake_cursor needs every 8ms to keep the
# fake cursor glued to the real mouse position. Under real movement the two
# were fighting for the CPU/GIL: the cursor would freeze while a burst of
# minimap re-renders ran back-to-back, then snap to the mouse's latest
# position once the backlog cleared — reported as the fake cursor "stutters
# then jumps" when the mouse is moved quickly. Rendering the minimap this
# often is unnecessary anyway (it's a 220x220 image); capping it well below
# the cursor-tracking rate keeps the main thread free often enough for the
# cursor poll to stay smooth without any visible loss of minimap fluidity.
MINIMAP_RENDER_MIN_INTERVAL_SECONDS = 0.075


class MapApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.profiles = load_profiles()
        self.profile = self._initial_profile()
        self.current: Position | None = None
        self.previous: Position | None = None
        self.older: Position | None = None
        self.source_image: Image.Image | None = None
        self.map_image: ImageTk.PhotoImage | None = None
        self.rendered_size: tuple[int, int] | None = None
        self.tray_icon: pystray.Icon | None = None
        self.global_escape = threading.Event()
        self.toggle_map_event = threading.Event()
        self.toggle_hud_event = threading.Event()
        self._toggle_map_vk = int(_load_config().get("toggle_map_key", DEFAULT_TOGGLE_MAP_VK))
        self._hud_hidden_by_user = False
        self._minimap_rect_cache: tuple[int, int, int, int] | None = None
        self._hud_body_rects_cache: tuple[tuple[int, int, int, int], ...] = ()
        self._last_mouse_pos: tuple[int, int] = (0, 0)
        self._fake_cursor_active = False
        self._real_cursor_visible_since: float | None = None
        self._hud_currently_visible = False
        self._fake_cursor = _FakeCursor(root)

        self.zoom = MIN_ZOOM
        self.center_nx = 0.5
        self.center_ny = 0.5
        self._view: tuple[float, float, float, float] | None = None
        self._pan_last: tuple[int, int] | None = None
        self._pending_hq_job: str | None = None

        self._zone_images: dict[str, Image.Image] = {}
        self._zone_visible: dict[str, bool] = {key: True for key, _label, _filename, _color in ZONE_LAYERS}
        self._zone_toggle_hitboxes: dict[str, tuple[float, float, float, float]] = {}

        self._islepilot_cred_path = DATA_ROOT / "islepilot.cred"
        self._islepilot_session: islepilot.IslePilotSession | None = None
        self._islepilot_steam_id: str | None = None
        self._islepilot_heading_deg: float | None = None
        self._islepilot_online = False
        self._islepilot_logging_in = False
        self._hud: IslePilotHud | None = None
        self._settings_window: tk.Toplevel | None = None

        # Local X/Y/Z/Yaw from the game's outbound UDP movement packets.
        # IslePilot remains the source for vitals/quests and a slow fallback.
        self._local_session: localtelemetry.LocalMovementSession | None = None
        self._local_state = "starting"
        self._local_last_update = 0.0
        self._local_heading_deg: float | None = None
        self._local_trail_last_update = 0.0
        self._local_minimap_render_last_update = 0.0
        self._npcap_prompted = False

        root.title("The-Maps")
        if APP_ICON_ICO.exists():
            root.iconbitmap(default=str(APP_ICON_ICO))
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.bind("<Tab>", self._hide_map_tab)

        self._build_ui()
        self._load_map_image()
        self._redraw()
        self._start_tray()
        threading.Thread(target=self._keyboard_hook_loop, daemon=True).start()
        threading.Thread(target=self._mouse_hook_loop, daemon=True).start()
        root.withdraw()
        self._poll_hotkey_events()
        self._start_local_telemetry()
        root.after(1000, self._prompt_for_npcap_if_needed)

        saved_credentials = islepilot.load_credentials(self._islepilot_cred_path)
        if saved_credentials:
            self._islepilot_start_session(*saved_credentials)
        self._poll_hud_visibility()
        self._poll_fake_cursor()
        self._update_notified = False
        self.root.after(5000, self._check_for_update)

    def _initial_profile(self) -> MapProfile:
        selected = _load_config().get("map", "")
        return next((p for p in self.profiles if p.profile_id == selected), self.profiles[0])

    def _build_ui(self) -> None:
        self.canvas = tk.Canvas(self.root, background="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_pan_end)
        self.canvas.bind("<Double-Button-1>", self._on_reset_view)

    def _size_map_window(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        if self.source_image:
            image_width, image_height = self.source_image.size
        else:
            image_width = image_height = 1
        scale = min(screen_width / image_width, screen_height / image_height)
        width = max(1, int(image_width * scale))
        height = max(1, int(image_height * scale))
        left = (screen_width - width) // 2
        top = (screen_height - height) // 2
        self.root.geometry(f"{width}x{height}+{left}+{top}")

    def _start_tray(self) -> None:
        if APP_ICON_PNG.exists():
            image = Image.open(APP_ICON_PNG).convert("RGBA")
        else:
            image = Image.new("RGBA", (64, 64), "#10191d")
        menu = pystray.Menu(
            pystray.MenuItem("Settings", self._tray_open_settings, default=True),
            pystray.MenuItem("Exit The-Maps", self._tray_exit),
        )
        self.tray_icon = pystray.Icon("The-Maps", image, "The-Maps", menu)
        self.tray_icon.run_detached()

    def _tray_open_settings(self, _icon=None, _item=None) -> None:
        self.root.after(0, self._open_settings)

    def _tray_exit(self, _icon=None, _item=None) -> None:
        # Belt-and-suspenders: _exit() below is meant to be unmissable, but
        # it's scheduled via root.after(0, ...) onto the Tk mainloop — if
        # that loop is itself wedged (e.g. stuck inside a modal messagebox()
        # call that ended up rendered behind The Isle's fullscreen/topmost
        # window — see _show_message — so nobody could see it to click OK),
        # the scheduled callback never runs and Exit silently does nothing.
        # This watchdog force-terminates the process a few seconds later no
        # matter what, so "Exit The-Maps" always actually exits.
        threading.Thread(target=self._force_exit_watchdog, daemon=True).start()
        self.root.after(0, self._exit)

    @staticmethod
    def _force_exit_watchdog() -> None:
        time.sleep(3.0)
        os._exit(1)

    def _show_message(
        self, kind: str, title: str, message: str,
    ) -> bool | None:
        """Show a messagebox dialog that is guaranteed to appear in front of
        The Isle, and never leave the app stuck.

        tkinter.messagebox dialogs are NOT topmost by default. Since The-Maps'
        whole job is to sit on top of a fullscreen, topmost game window, a
        plain messagebox call here can render *behind* the game — completely
        invisible to the player — while still blocking Tk's event loop until
        someone clicks it. In practice that froze the entire app (HUD stopped
        updating, hotkeys stopped responding, and even "Exit The-Maps" from
        the tray stopped working, since Exit just schedules a callback that
        can't run until the invisible modal dialog is dismissed) until the
        process was killed from Task Manager — reported as "it crashes and
        I can't close it" after playing for a while (long enough for the
        IslePilot session-expired warning or the once-a-day update check to
        fire on their own, unprompted, while the game had focus).

        Anchoring the dialog to a throwaway topmost Toplevel keeps it above
        the game so it's always visible and dismissible.
        """
        anchor = tk.Toplevel(self.root)
        anchor.overrideredirect(True)
        anchor.attributes("-topmost", True)
        anchor.geometry("1x1+0+0")
        try:
            anchor.deiconify()
            anchor.lift()
            anchor.focus_force()
            func = {
                "info": messagebox.showinfo,
                "warning": messagebox.showwarning,
                "error": messagebox.showerror,
                "askyesno": messagebox.askyesno,
            }[kind]
            return func(title, message, parent=anchor)
        finally:
            try:
                anchor.destroy()
            except tk.TclError:
                pass

    def _exit(self) -> None:
        # Best-effort cleanup only — pystray's tray_icon.stop() has been seen
        # to hang on Windows (its own message-loop thread not responding in
        # time), which would freeze the whole app on exit. Run it on a
        # throwaway daemon thread instead of waiting on it, and hard-exit
        # the process afterward so a stuck background thread (tray icon,
        # sniffer, IslePilot poll) can never prevent The-Maps from closing.
        #
        # Every step below is now individually guarded: an unhandled
        # exception raised directly inside a Tk after()-scheduled callback
        # like this one is silently swallowed by Tk's default callback
        # exception handler — it does NOT propagate, crash the app, or stop
        # the mainloop, it just aborts *this* callback partway through. That
        # meant a failure in any single cleanup step here could leave
        # os._exit(0) never reached, and the process quietly stuck running
        # in the background with a hidden window — indistinguishable from a
        # hang/crash to the user. Wrapping each step means one failing step
        # can never prevent the next one (or the final hard exit) from
        # running.
        try:
            if self.tray_icon:
                threading.Thread(target=self.tray_icon.stop, daemon=True).start()
        except Exception:
            pass
        try:
            self._islepilot_stop_session()
        except Exception:
            pass
        try:
            self._stop_local_telemetry()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            _restore_timer_resolution()
        except Exception:
            pass
        os._exit(0)

    def _show_map(self) -> None:
        self._size_map_window()
        self.root.attributes("-topmost", True)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._redraw()

    def _hide_map(self, _event=None) -> None:
        self.root.withdraw()

    def _hide_map_tab(self, _event=None):
        self._hide_map()
        return "break"

    def _toggle_map(self) -> None:
        if self.root.state() == "normal":
            self._hide_map()
        elif islepilot.is_game_foreground():
            # Only ever *open* the map while The Isle is the active window —
            # the hotkey is a global hook, so without this a keystroke typed
            # into a text chat or any other app while alt-tabbed out would
            # pop the map open unexpectedly.
            self._show_map()

    def _poll_hotkey_events(self) -> None:
        if self.global_escape.is_set():
            self.global_escape.clear()
            if self.root.state() == "normal":
                self._hide_map()
        if self.toggle_map_event.is_set():
            self.toggle_map_event.clear()
            self._toggle_map()
        if self.toggle_hud_event.is_set():
            self.toggle_hud_event.clear()
            # Gated on game-foreground for the same reason as M (see
            # _toggle_map): a global hook fires on 'n' typed anywhere,
            # including in a chat box or another app entirely.
            if islepilot.is_game_foreground():
                self._hud_hidden_by_user = not self._hud_hidden_by_user
        self.root.after(20, self._poll_hotkey_events)

    def _keyboard_hook_loop(self) -> None:
        class KeyboardEvent(ctypes.Structure):
            _fields_ = (
                ("vk_code", wintypes.DWORD),
                ("scan_code", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra_info", ctypes.c_size_t),
            )

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        @hook_proc_type
        def keyboard_hook(code, message, data):
            if code >= 0 and message in (0x0100, 0x0104):
                event = ctypes.cast(data, ctypes.POINTER(KeyboardEvent)).contents
                if event.vk_code == VK_TAB:
                    self.global_escape.set()
                elif event.vk_code == self._toggle_map_vk:
                    self.toggle_map_event.set()
                elif event.vk_code == VK_N:
                    self.toggle_hud_event.set()
            return user32.CallNextHookEx(None, code, message, data)

        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, hook_proc_type, ctypes.c_void_p, wintypes.DWORD
        )
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        hook = user32.SetWindowsHookExW(
            13, keyboard_hook, kernel32.GetModuleHandleW(None), 0
        )
        if not hook:
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _apply_minimap_wheel(self, delta: int) -> None:
        if self._hud is not None:
            self._hud.minimap.zoom_by_delta(delta)

    def _mouse_hook_loop(self) -> None:
        """Low-level mouse hook, mirroring _keyboard_hook_loop.

        Records every event's screen position into self._last_mouse_pos — a
        plain tuple write, safe to read from the main thread without a lock
        (GIL makes the assignment atomic) — kept only as incidental/debug
        state now. It used to double as the fake cursor's position source,
        on the theory that this hook sees every real mouse-move event as it
        happens, cheaper than polling GetCursorInfo. That backfired: a
        low-level hook's callback needs the GIL to run even a single tuple
        assignment, and Windows silently (and permanently — no notification)
        removes a low-level hook whose callback doesn't return within
        LowLevelHooksTimeout, which main-thread GIL contention could trip
        under load. See _poll_fake_cursor for the fix (poll
        GetCursorInfo directly instead, same as the shipped gw2-cursor tool
        does for this exact kind of overlay).

        The one thing this hook is still actually relied on for: the minimap
        body window is click-through (see _make_click_through) so it never
        receives WM_MOUSEWHEEL itself — this watches for wheel events over
        the minimap's last-known screen rect instead, applies the zoom on
        the main thread, and swallows the event so it doesn't also reach
        the game underneath.

        Must never call into Tk from this thread beyond the existing
        root.after(0, ...) scheduling for the wheel case."""

        class Point(ctypes.Structure):
            _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))

        class MouseEvent(ctypes.Structure):
            _fields_ = (
                ("pt", Point),
                ("mouse_data", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra_info", ctypes.c_size_t),
            )

        WM_MOUSEWHEEL = 0x020A

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        @hook_proc_type
        def mouse_hook(code, message, data):
            if code >= 0:
                event = ctypes.cast(data, ctypes.POINTER(MouseEvent)).contents
                self._last_mouse_pos = (event.pt.x, event.pt.y)
                if message == WM_MOUSEWHEEL:
                    rect = self._minimap_rect_cache
                    if rect is not None:
                        x1, y1, x2, y2 = rect
                        if x1 <= event.pt.x <= x2 and y1 <= event.pt.y <= y2:
                            delta = ctypes.c_short(event.mouse_data >> 16).value
                            self.root.after(0, lambda d=delta: self._apply_minimap_wheel(d))
                            return 1
            return user32.CallNextHookEx(None, code, message, data)

        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, hook_proc_type, ctypes.c_void_p, wintypes.DWORD
        )
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        hook = user32.SetWindowsHookExW(
            14, mouse_hook, kernel32.GetModuleHandleW(None), 0
        )
        if not hook:
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _select_profile(self, selected_name: str) -> None:
        self.profile = next(p for p in self.profiles if p.name == selected_name)
        self._local_heading_deg = None
        self._islepilot_heading_deg = None
        _save_config_update(map=self.profile.profile_id)
        self._load_map_image()
        self._redraw()

    def _open_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.deiconify()
            self._settings_window.lift()
            self._settings_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._settings_window = window

        def on_close() -> None:
            self._settings_window = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", on_close)
        window.title(f"The-Maps Settings — v{APP_VERSION}")
        window.resizable(False, False)
        # No -topmost here: it's a config dialog, not an in-game overlay,
        # and Windows has a well-known quirk where a Toplevel left topmost
        # stops responding to its own native minimize button.
        _ensure_taskbar_button(window)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Map hiển thị:").grid(row=0, column=0, sticky="w")
        choice = ttk.Combobox(
            frame,
            state="readonly",
            values=[profile.name for profile in self.profiles],
            width=32,
        )
        choice.set(self.profile.name)
        choice.grid(row=1, column=0, pady=(6, 14))

        def save() -> None:
            self._select_profile(choice.get())
            on_close()

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, sticky="ew")
        # Uses Tk's own iconify() rather than relying on the native
        # titlebar minimize button, which has been unreliable here.
        ttk.Button(button_row, text="Thu nhỏ", command=window.iconify).pack(side="left")
        ttk.Button(button_row, text="Lưu", command=save).pack(side="right")
        ttk.Separator(frame, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=14)

        ttk.Label(frame, text="Phím tắt hiện/ẩn bản đồ lớn:").grid(row=4, column=0, sticky="w")
        hotkey_var = tk.StringVar(value=_vk_display_name(self._toggle_map_vk))
        hotkey_row = ttk.Frame(frame)
        hotkey_row.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        ttk.Label(hotkey_row, textvariable=hotkey_var, foreground="#4caf50").pack(side="left")
        change_hotkey_button = ttk.Button(hotkey_row, text="Đổi phím tắt")
        change_hotkey_button.pack(side="right")

        def on_hotkey_captured(event) -> None:
            window.unbind("<KeyPress>", capture_bind_id[0])
            change_hotkey_button.configure(state="normal")
            vk = event.keycode
            if vk == VK_TAB:
                hotkey_var.set(_vk_display_name(self._toggle_map_vk) + "  (Tab đã dùng để đóng map, chọn phím khác)")
                return
            if vk == VK_N:
                hotkey_var.set(_vk_display_name(self._toggle_map_vk) + "  (N đã dùng để bật/tắt HUD, chọn phím khác)")
                return
            self._toggle_map_vk = vk
            _save_config_update(toggle_map_key=vk)
            hotkey_var.set(_vk_display_name(vk))

        capture_bind_id: list[str] = [""]

        def start_hotkey_capture() -> None:
            change_hotkey_button.configure(state="disabled")
            hotkey_var.set("Nhấn phím bất kỳ…")
            capture_bind_id[0] = window.bind("<KeyPress>", on_hotkey_captured)

        change_hotkey_button.configure(command=start_hotkey_capture)
        ttk.Separator(frame, orient="horizontal").grid(row=6, column=0, sticky="ew", pady=14)

        ttk.Label(frame, text="IslePilot (chỉ số, nhiệm vụ + vị trí fallback):").grid(
            row=7, column=0, sticky="w"
        )
        status_var = tk.StringVar(value=self._islepilot_status_text())
        ttk.Label(frame, textvariable=status_var, foreground="#4caf50").grid(
            row=8, column=0, sticky="w", pady=(2, 6)
        )

        islepilot_button_row = ttk.Frame(frame)
        islepilot_button_row.grid(row=9, column=0, sticky="ew")
        connect_button = ttk.Button(islepilot_button_row)
        manual_token_button = ttk.Button(islepilot_button_row, text="Nhập token thủ công")

        def refresh() -> None:
            status_var.set(self._islepilot_status_text())
            connected = self._islepilot_connected()
            connect_button.configure(
                text="Ngắt kết nối IslePilot" if connected else "Đăng nhập Steam qua IslePilot"
            )
            manual_token_button.configure(state="disabled" if connected else "normal")

        def on_connect_click() -> None:
            if self._islepilot_connected():
                self._islepilot_disconnect()
                refresh()
                return
            connect_button.configure(state="disabled")
            status_var.set("Đang mở cửa sổ đăng nhập Steam…")

            def done() -> None:
                connect_button.configure(state="normal")
                refresh()

            self._islepilot_login_async(done)

        def on_manual_token_click() -> None:
            self._open_manual_token_dialog(window, refresh)

        connect_button.configure(command=on_connect_click)
        connect_button.pack(side="left")
        manual_token_button.configure(command=on_manual_token_click)
        manual_token_button.pack(side="left", padx=(8, 0))
        refresh()

        ttk.Separator(frame, orient="horizontal").grid(row=10, column=0, sticky="ew", pady=14)
        ttk.Label(frame, text="Local position realtime (Npcap):").grid(
            row=11, column=0, sticky="w"
        )
        local_status_var = tk.StringVar(value=self._local_status_text())
        ttk.Label(frame, textvariable=local_status_var, foreground="#607d8b").grid(
            row=12, column=0, sticky="w", pady=(2, 6)
        )

        local_button_row = ttk.Frame(frame)
        local_button_row.grid(row=13, column=0, sticky="ew")
        ttk.Button(
            local_button_row,
            text="Kiểm tra lại",
            command=lambda: (self._retry_local_telemetry(), local_status_var.set(self._local_status_text())),
        ).pack(side="left")
        ttk.Button(
            local_button_row,
            text="Download && Install Npcap",
            command=self._install_npcap_async,
        ).pack(side="right")

        def refresh_local_status() -> None:
            if not window.winfo_exists():
                return
            local_status_var.set(self._local_status_text())
            window.after(500, refresh_local_status)

        refresh_local_status()

        ttk.Separator(frame, orient="horizontal").grid(row=14, column=0, sticky="ew", pady=14)
        ttk.Button(
            frame,
            text="Subscribe Please",
            command=lambda: webbrowser.open(YOUTUBE_URL),
        ).grid(row=15, column=0, sticky="ew")
        ttk.Button(
            frame,
            text="Join Discord",
            command=lambda: webbrowser.open(DISCORD_URL),
        ).grid(row=16, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            frame, text=f"The-Maps v{APP_VERSION}", foreground="#9e9e9e",
        ).grid(row=17, column=0, sticky="e", pady=(10, 0))
        # No grab_set(): Tk's modal grab on Windows is known to conflict
        # with the native caption buttons' own internal click-tracking,
        # which is what was making minimize unresponsive. Settings doesn't
        # need to block the rest of the app anyway.
        window.focus_force()

    def _local_position_fresh(self) -> bool:
        return (
            self._local_last_update > 0.0
            and time.monotonic() - self._local_last_update <= LOCAL_POSITION_FRESH_SECONDS
        )

    def _local_status_text(self) -> str:
        labels = {
            "starting": "Local realtime: đang khởi động",
            "npcap_missing": "Local realtime: thiếu Npcap · có thể cài tự động",
            "waiting_game": "Local realtime: chờ The Isle",
            "waiting_packets": "Local realtime: chờ movement packets",
            "tracking": "Local realtime: đang nhận X/Y/Yaw",
            "capture_error": "Local realtime: lỗi mở Npcap",
        }
        return labels.get(self._local_state, f"Local realtime: {self._local_state}")

    def _start_local_telemetry(self) -> None:
        self._stop_local_telemetry()

        def on_sample(sample: localtelemetry.LocalMovementSample) -> None:
            self.root.after(0, lambda s=sample: self._apply_local_movement(s))

        def on_state(state: str) -> None:
            self.root.after(0, lambda s=state: self._set_local_state(s))

        self._local_session = localtelemetry.LocalMovementSession(on_sample, on_state)
        self._local_session.start()

    def _stop_local_telemetry(self) -> None:
        if self._local_session is not None:
            self._local_session.stop()
            self._local_session = None

    def _set_local_state(self, state: str) -> None:
        self._local_state = state

    def _prompt_for_npcap_if_needed(self) -> None:
        if self._npcap_prompted or localtelemetry.npcap_installed():
            return
        self._npcap_prompted = True
        if self._show_message(
            "askyesno",
            "The-Maps · Realtime position",
            "Minimap realtime cần Npcap để đọc movement packets của The Isle.\n\n"
            "Npcap chưa được cài. The-Maps có thể tự tải installer trực tiếp "
            "từ npcap.com, kiểm tra chữ ký số của Nmap Software LLC rồi mở "
            "trình cài đặt cho bạn.\n\n"
            "Tải và cài Npcap ngay?",
        ):
            self._install_npcap_async()

    def _install_npcap_async(self) -> None:
        if localtelemetry.npcap_installed():
            self._show_message("info", "The-Maps", "Npcap đã được cài trên máy.")
            self._retry_local_telemetry()
            return

        progress_window = tk.Toplevel(self.root)
        progress_window.title("The-Maps · Installing Npcap")
        progress_window.resizable(False, False)
        progress_window.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = ttk.Frame(progress_window, padding=16)
        frame.pack(fill="both", expand=True)

        status_var = tk.StringVar(
            value="Đang tìm bản Npcap mới nhất trên npcap.com…"
        )
        ttk.Label(
            frame,
            textvariable=status_var,
            width=54,
            wraplength=410,
        ).pack(anchor="w")

        progress = ttk.Progressbar(frame, length=410, mode="indeterminate")
        progress.pack(fill="x", pady=(12, 0))
        progress.start(12)

        def set_status(text: str) -> None:
            self.root.after(0, lambda: status_var.set(text))

        def on_progress(downloaded: int, total: int | None) -> None:
            if total and total > 0:
                percent = min(100.0, downloaded * 100.0 / total)
                set_status(f"Đang tải Npcap từ npcap.com… {percent:.0f}%")
            else:
                set_status(
                    f"Đang tải Npcap từ npcap.com… {downloaded / 1024:.0f} KB"
                )

        def worker() -> None:
            reboot_required = False
            try:
                success, message, reboot_required = (
                    localtelemetry.install_npcap_from_official_site(on_progress)
                )
            except Exception as exc:
                success = False
                message = str(exc)

            def finish() -> None:
                if progress_window.winfo_exists():
                    progress.stop()
                    progress_window.destroy()

                if success and reboot_required:
                    self._show_message(
                        "info",
                        "The-Maps",
                        f"{message}\n\n"
                        "Hãy khởi động lại Windows rồi mở The-Maps lại để "
                        "bật minimap realtime.",
                    )
                elif success:
                    self._show_message("info", "The-Maps", message)
                    self._retry_local_telemetry()
                else:
                    self._show_message(
                        "error",
                        "The-Maps · Npcap",
                        "Không cài được Npcap.\n\n"
                        f"{message}\n\n"
                        "The-Maps vẫn có thể dùng IslePilot REST làm vị trí fallback.",
                    )

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _retry_local_telemetry(self) -> None:
        self._local_state = "starting"
        self._start_local_telemetry()

    def _apply_local_movement(
        self,
        sample: localtelemetry.LocalMovementSample,
    ) -> None:
        self._local_last_update = time.monotonic()
        self._local_state = "tracking"

        # Npcap decodes the same raw Unreal world Location (engine X/Y/Z
        # order) that IslePilot's backend also reads server-side as
        # pos_x/pos_y. v1.3's IslePilotAxisOrderTests proved that raw world
        # X/Y must be swapped to match this app's clipboard/world Position
        # convention under swap_axes=True — the same swap applies here.
        position = Position(sample.y, sample.x, sample.z)
        if (
            position != self.current
            and self._local_last_update - self._local_trail_last_update
            >= LOCAL_TRAIL_MIN_INTERVAL_SECONDS
        ):
            self.older = self.previous
            self.previous = self.current
            self._local_trail_last_update = self._local_last_update
        self.current = position
        self._local_heading_deg = self.profile.transform_yaw(sample.yaw)

        if self._hud is None:
            self._hud = IslePilotHud(self.root)

        # The minimap is the hot path, updated from the local packet without
        # waiting for the ~5s IslePilot snapshot — but the actual PIL re-render
        # (crop/composite/resize + a fresh PhotoImage) is throttled well below
        # the sample rate; see MINIMAP_RENDER_MIN_INTERVAL_SECONDS above for
        # why (it was starving the fake-cursor tracking loop of CPU/GIL time).
        if (
            self._local_last_update - self._local_minimap_render_last_update
            >= MINIMAP_RENDER_MIN_INTERVAL_SECONDS
        ):
            self._local_minimap_render_last_update = self._local_last_update
            self._hud.update_map(
                self.source_image,
                self.profile,
                position.x,
                position.y,
                self._local_heading_deg,
                zone_images=self._active_zone_images(),
            )

        if self.root.state() == "normal":
            self._redraw()

    def _islepilot_status_text(self) -> str:
        if self._islepilot_steam_id:
            return f"Đã kết nối · Steam ...{self._islepilot_steam_id[-4:]}"
        return "Chưa kết nối"

    def _islepilot_connected(self) -> bool:
        return self._islepilot_steam_id is not None

    def _islepilot_login_async(self, on_done) -> None:
        if self._islepilot_logging_in:
            return
        self._islepilot_logging_in = True

        def worker() -> None:
            result = islepilot.run_login_subprocess()

            def finish() -> None:
                self._islepilot_logging_in = False
                if result is not None:
                    steam_id, token = result
                    islepilot.save_credentials(self._islepilot_cred_path, steam_id, token)
                    self._islepilot_start_session(steam_id, token)
                on_done()

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _open_manual_token_dialog(self, parent: tk.Misc, on_connected) -> None:
        dialog = tk.Toplevel(parent)
        dialog.title("Nhập token IslePilot")
        dialog.resizable(False, False)
        dialog.transient(parent)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Dán token overlay từ islepilot.eu (thay cho đăng nhập Steam):",
            wraplength=340,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        token_var = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=token_var, width=48, show="*")
        entry.grid(row=1, column=0, pady=(6, 6), sticky="ew")
        entry.focus_set()

        error_var = tk.StringVar()
        ttk.Label(frame, textvariable=error_var, foreground="#e53935", wraplength=340, justify="left").grid(
            row=2, column=0, sticky="w"
        )

        button_row = ttk.Frame(frame)
        button_row.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        def on_cancel() -> None:
            dialog.destroy()

        def on_connect() -> None:
            token = token_var.get().strip()
            if not token:
                error_var.set("Token trống.")
                return
            connect_btn.configure(state="disabled")
            cancel_btn.configure(state="disabled")
            error_var.set("Đang kiểm tra token…")

            def worker() -> None:
                steam_id = islepilot.verify_token(token)

                def finish() -> None:
                    if not dialog.winfo_exists():
                        return
                    if steam_id is None:
                        connect_btn.configure(state="normal")
                        cancel_btn.configure(state="normal")
                        error_var.set("Token không hợp lệ hoặc đã hết hạn.")
                        return
                    islepilot.save_credentials(self._islepilot_cred_path, steam_id, token)
                    self._islepilot_start_session(steam_id, token)
                    dialog.destroy()
                    on_connected()

                self.root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        cancel_btn = ttk.Button(button_row, text="Hủy", command=on_cancel)
        cancel_btn.pack(side="right", padx=(0, 8))
        connect_btn = ttk.Button(button_row, text="Kết nối", command=on_connect)
        connect_btn.pack(side="right")

        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        dialog.bind("<Return>", lambda _event: on_connect())

    def _islepilot_start_session(self, steam_id: str, token: str) -> None:
        self._islepilot_stop_session()
        self._islepilot_steam_id = steam_id
        self._islepilot_heading_deg = None
        self._islepilot_online = False

        def on_status(status: islepilot.IslePilotStatus) -> None:
            self.root.after(0, lambda: self._apply_islepilot_status(status))

        def on_error(_reason: str) -> None:
            self.root.after(0, self._islepilot_handle_expired)

        self._islepilot_session = islepilot.IslePilotSession(token, on_status, on_error)
        self._islepilot_session.start()
        if self._hud is None:
            self._hud = IslePilotHud(self.root)

    def _islepilot_stop_session(self) -> None:
        if self._islepilot_session is not None:
            self._islepilot_session.stop()
            self._islepilot_session = None

    def _islepilot_disconnect(self) -> None:
        self._islepilot_stop_session()
        islepilot.clear_credentials(self._islepilot_cred_path)
        self._islepilot_steam_id = None
        self._islepilot_heading_deg = None
        self._islepilot_online = False
        if self._hud is not None:
            self._hud.hide()
            self._hud.destroy()
            self._hud = None

    def _islepilot_handle_expired(self) -> None:
        was_connected = self._islepilot_connected()
        self._islepilot_disconnect()
        if was_connected:
            self._show_message(
                "warning",
                "The-Maps", "Phiên IslePilot đã hết hạn. Vào Settings để đăng nhập lại."
            )

    def _apply_islepilot_status(self, status: islepilot.IslePilotStatus) -> None:
        self._islepilot_online = status.online

        # IslePilot is authoritative for vitals / quests / online state.
        if self._hud is None and (status.online or self._local_position_fresh()):
            self._hud = IslePilotHud(self.root)

        if self._hud is not None:
            self._hud.update_vitals(status)
            self._hud.update_quests(status)

        # Position/yaw from /api/overlay/me only act as fallback. Live testing
        # showed that backend snapshot changes roughly every ~5 seconds, while
        # the local Npcap stream is ~8-10 Hz. Never let slow REST pull a fresh
        # local marker backwards.
        if self._local_position_fresh():
            return

        heading_changed = False
        if status.pos_yaw is not None:
            new_heading = self.profile.transform_yaw(status.pos_yaw)
            heading_changed = new_heading != self._islepilot_heading_deg
            self._islepilot_heading_deg = new_heading

        position: Position | None = None
        position_changed = False
        if status.pos_x is not None and status.pos_y is not None:
            # IslePilot's API uses the opposite horizontal convention from
            # this app's clipboard/world Position representation.
            position = Position(status.pos_y, status.pos_x, status.pos_z or 0.0)
            if position != self.current:
                self.current = position
                position_changed = True

        if (position_changed or heading_changed) and self.root.state() == "normal":
            self._redraw()

        draw_position = position or self.current
        if self._hud is not None and draw_position is not None:
            heading = (
                self._islepilot_heading_deg
                if self._islepilot_heading_deg is not None
                else 0.0
            )
            self._hud.update_map(
                self.source_image,
                self.profile,
                draw_position.x,
                draw_position.y,
                heading,
                zone_images=self._active_zone_images(),
            )

    def _poll_hud_visibility(self) -> None:
        was_visible = self._hud_currently_visible
        if self._hud is not None:
            local_live = self._local_position_fresh()
            has_live_source = local_live or self._islepilot_online
            if has_live_source and islepilot.is_game_foreground() and not self._hud_hidden_by_user:
                self._hud.show(show_quests=self._islepilot_online)
                self._minimap_rect_cache = self._hud.minimap.canvas_screen_rect()
                self._hud_body_rects_cache = tuple(
                    rect for rect in (
                        self._hud.minimap.body_screen_rect(),
                        self._hud.quests.body_screen_rect(),
                    )
                    if rect is not None
                )
                self._hud_currently_visible = True
            else:
                self._hud.hide()
                self._minimap_rect_cache = None
                self._hud_body_rects_cache = ()
                self._hud_currently_visible = False
        else:
            self._minimap_rect_cache = None
            self._hud_body_rects_cache = ()
            self._hud_currently_visible = False
        # Only re-assert stacking on the actual hidden->visible transition
        # (see _FakeCursor.raise_above_hud) — the HUD panels themselves
        # already skip redundant deiconify() calls now, but this covers
        # the one legitimate case where the fake cursor needs to be pushed
        # back above them: the moment the HUD (re)appears.
        if self._hud_currently_visible and not was_visible:
            self._fake_cursor.raise_above_hud()
        self.root.after(FOREGROUND_POLL_MS, self._poll_hud_visibility)

    def _poll_fake_cursor(self) -> None:
        """Single fast loop (FAKE_CURSOR_TRACK_MS) that both decides
        show/hide *and* tracks position, from one GetCursorInfo call.

        This used to be two separate loops: a "decision-free" position
        tracker at ~120 Hz following self._last_mouse_pos (written by the
        WH_MOUSE_LL hook's own thread), and a show/hide decision at a
        slower ~30 Hz calling GetCursorInfo. That split caused two distinct,
        reported symptoms once the hook-derived position was replaced with a
        fresh GetCursorInfo query (see below) for tracking but the decision
        loop was left at its slower rate with a multi-tick "stay outside the
        HUD for N ticks" hide-debounce:

        - Entering the HUD quickly appeared to lag, because the show
          decision could only fire on the next ~33 ms decision tick, not the
          ~8 ms tracking tick.
        - Leaving the HUD quickly showed the fake cursor sliding out past
          the HUD's edge before it vanished, because the hide-debounce (added
          to tolerate a *stale* hook position) kept it latched active for a
          few more ticks after the fresh, accurate position had already left
          the HUD rect — and the (already-fixed, now GetCursorInfo-driven)
          position tracker kept faithfully moving it there in the meantime.

        Merging both into one loop at the fast rate fixes both: the show/
        hide decision now runs at the same ~120 Hz as tracking (no more lag
        entering), and since GetCursorInfo is queried fresh every single
        tick — never a stale hook value — there's no more staleness for a
        hide-debounce to compensate for, so hiding goes back to being
        immediate on the very next real "outside the HUD" sample (no more
        overshoot leaving). The one debounce that's still legitimate and
        kept is FAKE_CURSOR_REAL_VISIBLE_DEBOUNCE_SECONDS: that guards
        against a single transient GetCursorInfo sample reporting the real
        cursor visible while still inside the HUD, which is a different,
        genuine sensor-noise case, not staleness.

        gw2-cursor (github.com/fritzw/gw2-cursor), a shipped tool solving
        this exact problem for a different game, does the same thing: no
        hook for position — poll GetCursorPos-style state at a high fixed
        rate (120 Hz in its case) and drive the overlay from that alone. The
        low-level mouse hook here is kept only for its other job (detecting
        wheel events over the minimap, which the click-through body never
        receives), not for anything cursor-position-related."""
        state = _get_cursor_state()
        if state is None:
            # Fail safe toward "stay out of the way" if the query fails.
            self._set_fake_cursor_active(False)
            self.root.after(FAKE_CURSOR_TRACK_MS, self._poll_fake_cursor)
            return

        real_showing, x, y = state
        mouse_in_hud = any(
            x1 <= x <= x2 and y1 <= y <= y2 for x1, y1, x2, y2 in self._hud_body_rects_cache
        )
        if not mouse_in_hud:
            self._set_fake_cursor_active(False)
        else:
            if not self._fake_cursor_active:
                if not real_showing:
                    self._set_fake_cursor_active(True, x, y)
            elif not real_showing:
                self._real_cursor_visible_since = None
            else:
                now = time.monotonic()
                if self._real_cursor_visible_since is None:
                    self._real_cursor_visible_since = now
                elif now - self._real_cursor_visible_since >= FAKE_CURSOR_REAL_VISIBLE_DEBOUNCE_SECONDS:
                    self._set_fake_cursor_active(False)

        if self._fake_cursor_active:
            self._fake_cursor.move_to(x, y)

        self.root.after(FAKE_CURSOR_TRACK_MS, self._poll_fake_cursor)

    def _set_fake_cursor_active(
        self, active: bool, x: int | None = None, y: int | None = None,
    ) -> None:
        if active == self._fake_cursor_active:
            return
        self._fake_cursor_active = active
        self._real_cursor_visible_since = None
        if active:
            # Prefer the position the caller already has this tick (from its
            # own GetCursorInfo call) — falls back to a fresh query only if
            # it wasn't passed in, never self._last_mouse_pos (see
            # _poll_fake_cursor for why the hook-derived position isn't used
            # for placing the fake cursor).
            if x is None or y is None:
                state = _get_cursor_state()
                if state is not None:
                    _real_showing, x, y = state
            if x is not None and y is not None:
                self._fake_cursor.move_to(x, y)
            self._fake_cursor.show()
        else:
            self._fake_cursor.hide()

    def _check_for_update(self) -> None:
        threading.Thread(target=self._check_for_update_worker, daemon=True).start()
        self.root.after(UPDATE_CHECK_INTERVAL_MS, self._check_for_update)

    def _check_for_update_worker(self) -> None:
        try:
            response = requests.get(
                GITHUB_RELEASE_API, timeout=8.0,
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            latest_tag = response.json().get("tag_name")
        except (requests.RequestException, ValueError, OSError):
            return
        if latest_tag and latest_tag != RELEASE_TAG:
            self.root.after(0, lambda: self._notify_update_available(latest_tag))

    def _notify_update_available(self, latest_tag: str) -> None:
        if self._update_notified:
            return
        self._update_notified = True
        if self._show_message(
            "askyesno",
            "The-Maps",
            f"Đã có bản cập nhật mới trên GitHub ({latest_tag}). Mở trang tải về?",
        ):
            webbrowser.open(GITHUB_RELEASE_PAGE)

    def _load_map_image(self) -> None:
        self._cancel_hq_job()
        self.source_image = None
        self.map_image = None
        self.rendered_size = None
        self.zoom = MIN_ZOOM
        self.center_nx = 0.5
        self.center_ny = 0.5
        if self.profile.image_path:
            try:
                self.source_image = Image.open(self.profile.image_path).convert("RGB")
            except (OSError, ValueError):
                self.source_image = None
        self._zone_images = {}
        for key, path in self.profile.zone_image_paths.items():
            try:
                self._zone_images[key] = Image.open(path).convert("RGBA")
            except (OSError, ValueError):
                pass
        self._size_map_window()

    def _clamp_view(self) -> None:
        view_w = view_h = 1.0 / self.zoom
        max_left = max(0.0, 1.0 - view_w)
        max_top = max(0.0, 1.0 - view_h)
        left = min(max(self.center_nx - view_w / 2, 0.0), max_left)
        top = min(max(self.center_ny - view_h / 2, 0.0), max_top)
        self.center_nx = left + view_w / 2
        self.center_ny = top + view_h / 2
        self._view = (left, top, view_w, view_h)

    def _redraw(self, resample: int | None = None) -> None:
        canvas = self.canvas
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        canvas.delete("all")

        if self.source_image:
            self._clamp_view()
            view_left, view_top, view_w, view_h = self._view
            source_width, source_height = self.source_image.size
            crop_left = max(0, int(view_left * source_width))
            crop_top = max(0, int(view_top * source_height))
            crop_right = min(source_width, max(crop_left + 1, round((view_left + view_w) * source_width)))
            crop_bottom = min(source_height, max(crop_top + 1, round((view_top + view_h) * source_height)))
            crop_box = (crop_left, crop_top, crop_right, crop_bottom)
            resample_filter = resample if resample is not None else Image.Resampling.LANCZOS
            active_zone_keys = tuple(
                key for key, _label, _filename, _color in ZONE_LAYERS
                if self._zone_visible.get(key) and key in self._zone_images
            )
            cache_key = (crop_box, (width, height), resample_filter, active_zone_keys)
            if self.rendered_size != cache_key:
                cropped = self.source_image.crop(crop_box).convert("RGBA")
                for key in active_zone_keys:
                    cropped.alpha_composite(self._zone_images[key].crop(crop_box))
                resized = cropped.resize((width, height), resample_filter)
                self.map_image = ImageTk.PhotoImage(resized)
                self.rendered_size = cache_key
            canvas.create_image(width / 2, height / 2, image=self.map_image)
            self._placeholder_rect = None
        else:
            self._view = None
            margin = 24
            left, top = margin, margin
            map_width, map_height = width - margin * 2, height - margin * 2
            self._placeholder_rect = (left, top, map_width, map_height)
            canvas.create_rectangle(
                left, top, left + map_width, top + map_height,
                fill="#26343a", outline="#607d8b", width=2,
            )
            for step in range(1, 10):
                x = left + map_width * step / 10
                y = top + map_height * step / 10
                canvas.create_line(x, top, x, top + map_height, fill="#34474f")
                canvas.create_line(left, y, left + map_width, y, fill="#34474f")
            canvas.create_text(
                width / 2, height / 2,
                text=f"{self.profile.name}\nChưa có ảnh map",
                fill="#b0bec5", font=("Segoe UI", 16), justify="center",
            )

        self._canvas_w, self._canvas_h = width, height
        self._draw_zone_toggles()
        self._draw_history_path()
        if self.older:
            self._draw_marker(self.older, 5, "#71838b")
        if self.previous:
            self._draw_marker(self.previous, 8, "#55a8c9")
        if self.current:
            heading = self._current_heading_degrees()
            if heading is None:
                self._draw_marker(self.current, 12, "#ff5b45")
            else:
                x, y = self._pixel(self.current)
                _draw_heading_polygon(self.canvas, x, y, heading, 14, "#ff5b45")

    def _pixel(self, position: Position) -> tuple[float, float]:
        nx, ny = self.profile.to_normalized(position)
        return self._normalized_to_pixel(nx, ny)

    def _normalized_to_pixel(self, nx: float, ny: float) -> tuple[float, float]:
        if self._view is not None:
            view_left, view_top, view_w, view_h = self._view
            x = (nx - view_left) / view_w * self._canvas_w
            y = (ny - view_top) / view_h * self._canvas_h
            return x, y
        left, top, width, height = self._placeholder_rect
        return left + nx * width, top + ny * height

    def _active_zone_images(self) -> tuple["Image.Image", ...]:
        return tuple(
            self._zone_images[key]
            for key, _label, _filename, _color in ZONE_LAYERS
            if self._zone_visible.get(key) and key in self._zone_images
        )

    def _draw_zone_toggles(self) -> None:
        self._zone_toggle_hitboxes = {}
        if not self._zone_images:
            return
        margin = 12
        chip_h = 26
        x = margin
        y = self._canvas_h - margin - chip_h
        for key, label, _filename, color in ZONE_LAYERS:
            if key not in self._zone_images:
                continue
            active = self._zone_visible.get(key, False)
            text = f"{'✓' if active else '○'} {label}"
            text_id = self.canvas.create_text(
                x + 10, y + chip_h / 2, text=text,
                fill="#10191d" if active else "#cfd8dc",
                font=("Segoe UI", 9, "bold"), anchor="w",
            )
            bbox = self.canvas.bbox(text_id)
            chip_w = (bbox[2] - bbox[0]) + 20 if bbox else 90
            rect_id = self.canvas.create_rectangle(
                x, y, x + chip_w, y + chip_h,
                fill=color if active else "#1b262c", outline=color, width=1.5,
            )
            self.canvas.tag_lower(rect_id, text_id)
            self._zone_toggle_hitboxes[key] = (x, y, x + chip_w, y + chip_h)
            x += chip_w + 8

    def _draw_marker(self, position, radius, color):
        x, y = self._pixel(position)
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius,
            fill=color, outline="white", width=2,
        )

    def _draw_history_path(self):
        positions = [position for position in (self.older, self.previous, self.current) if position]
        if len(positions) < 2:
            return
        points: list[float] = []
        for position in positions:
            x, y = self._pixel(position)
            points.extend((x, y))
        self.canvas.create_line(
            *points,
            fill="#d4e3e8",
            width=2,
            dash=(3, 7),
        )

    def _current_heading_degrees(self) -> float | None:
        """Prefer local packet yaw, then IslePilot fallback, then movement."""
        if self._local_position_fresh() and self._local_heading_deg is not None:
            return self._local_heading_deg
        if self._islepilot_heading_deg is not None:
            return self._islepilot_heading_deg
        if self.current and self.previous:
            x1, y1 = self._pixel(self.previous)
            x2, y2 = self._pixel(self.current)
            dx, dy = x2 - x1, y2 - y1
            if math.hypot(dx, dy) < 2:
                return None
            return math.degrees(math.atan2(dx, -dy)) % 360.0
        return None

    def _schedule_hq_redraw(self) -> None:
        self._cancel_hq_job()
        self._pending_hq_job = self.root.after(HQ_REDRAW_DELAY_MS, self._hq_redraw)

    def _cancel_hq_job(self) -> None:
        if self._pending_hq_job is not None:
            try:
                self.root.after_cancel(self._pending_hq_job)
            except tk.TclError:
                pass
            self._pending_hq_job = None

    def _hq_redraw(self) -> None:
        self._pending_hq_job = None
        self._redraw()

    def _on_mouse_wheel(self, event) -> None:
        if not self.source_image or self._view is None:
            return
        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        view_left, view_top, view_w, view_h = self._view
        cursor_nx = view_left + (event.x / canvas_w) * view_w
        cursor_ny = view_top + (event.y / canvas_h) * view_h
        factor = ZOOM_STEP if event.delta > 0 else (1.0 / ZOOM_STEP)
        new_zoom = min(MAX_ZOOM, max(MIN_ZOOM, self.zoom * factor))
        if new_zoom == self.zoom:
            return
        self.zoom = new_zoom
        new_view_w = new_view_h = 1.0 / new_zoom
        self.center_nx = cursor_nx - (event.x / canvas_w - 0.5) * new_view_w
        self.center_ny = cursor_ny - (event.y / canvas_h - 0.5) * new_view_h
        self._redraw(resample=Image.Resampling.BILINEAR)
        self._schedule_hq_redraw()

    def _on_pan_start(self, event) -> None:
        for key, (x1, y1, x2, y2) in self._zone_toggle_hitboxes.items():
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self._zone_visible[key] = not self._zone_visible[key]
                self._redraw()
                return
        if not self.source_image:
            return
        self._pan_last = (event.x, event.y)

    def _on_pan_move(self, event) -> None:
        if not self.source_image or self._pan_last is None or self._view is None:
            return
        canvas_w = max(self.canvas.winfo_width(), 1)
        canvas_h = max(self.canvas.winfo_height(), 1)
        last_x, last_y = self._pan_last
        dx, dy = event.x - last_x, event.y - last_y
        self._pan_last = (event.x, event.y)
        _view_left, _view_top, view_w, view_h = self._view
        self.center_nx -= (dx / canvas_w) * view_w
        self.center_ny -= (dy / canvas_h) * view_h
        self._redraw(resample=Image.Resampling.BILINEAR)
        self._schedule_hq_redraw()

    def _on_pan_end(self, _event=None) -> None:
        self._pan_last = None

    def _on_reset_view(self, _event=None) -> None:
        self._cancel_hq_job()
        self.zoom = MIN_ZOOM
        self.center_nx = 0.5
        self.center_ny = 0.5
        self._redraw()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == islepilot.LOGIN_SUBPROCESS_FLAG:
        # Helper-process mode: run only the Steam login flow and report the
        # result on stdout. See islepilot.run_login_subprocess() for why
        # this needs to be a separate process rather than a thread.
        result = islepilot.login_via_steam()
        payload = {"steam_id": result[0], "token": result[1]} if result else None
        print(json.dumps(payload))
        return

    _raise_timer_resolution()
    root = tk.Tk()
    try:
        MapApp(root)
    except RuntimeError as exc:
        messagebox.showerror("The-Maps", str(exc))
        root.destroy()
        _restore_timer_resolution()
        return
    except Exception:
        crash_log = DATA_ROOT / "crash.log"
        try:
            DATA_ROOT.mkdir(parents=True, exist_ok=True)
            crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        messagebox.showerror(
            "The-Maps",
            "The-Maps gặp lỗi khi khởi động và không mở được.\n\n"
            f"Chi tiết lỗi đã được ghi vào:\n{crash_log}",
        )
        root.destroy()
        _restore_timer_resolution()
        return
    root.mainloop()


if __name__ == "__main__":
    main()