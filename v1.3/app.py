from __future__ import annotations

import json
import math
import os
import re
import sys
import ctypes
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from ctypes import wintypes

import pystray
import requests
from PIL import Image, ImageTk

import islepilot


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
APP_VERSION = "1.3.0"

# v1.3 and v2.0 ship together in one combined GitHub Release — bump this
# whenever a new combined release is cut so the update check below fires.
RELEASE_TAG = "v2"
GITHUB_RELEASE_API = "https://api.github.com/repos/EmpireOcean/The-Maps/releases/latest"
GITHUB_RELEASE_PAGE = "https://github.com/EmpireOcean/The-Maps/releases/latest"
UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000

VK_TAB = 0x09
MIN_ZOOM = 1.0
MAX_ZOOM = 6.0
ZOOM_STEP = 1.15
HQ_REDRAW_DELAY_MS = 120


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


NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
EVRIMA_PATTERN = re.compile(
    rf"^\s*({NUMBER})\s*,\s*({NUMBER})\s*,\s*({NUMBER})\s*$"
)
LEGACY_PATTERN = re.compile(
    rf"Lat\s*:\s*({NUMBER}).*?Long\s*:\s*({NUMBER}).*?Alt\s*:\s*({NUMBER})",
    re.IGNORECASE | re.DOTALL,
)


def parse_coordinate(text: str) -> Position | None:
    match = EVRIMA_PATTERN.match(text.strip()) or LEGACY_PATTERN.search(text)
    if not match:
        return None
    try:
        values = [float(value.replace(",", "")) for value in match.groups()]
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return Position(*values)


def load_profiles() -> list[MapProfile]:
    profiles: list[MapProfile] = []
    for manifest in sorted(MAPS_DIR.glob("*/map.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            bounds = data["world_bounds"]
            image = manifest.parent / data["image"] if data.get("image") else None
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


def _format_stat(value: float) -> str:
    # A hatchling's max pool can be a small fraction (e.g. 0.4) that would
    # otherwise truncate to a confusing "0" — keep one decimal below 10.
    if abs(value) < 10:
        return f"{value:.1f}"
    return str(int(round(value)))


HUD_MARGIN = 12
MINI_MAP_SIZE = 220
MINI_MAP_CROP_FRACTION = 0.16
QUEST_PANEL_WIDTH = 260
HUD_BG = "#10191d"
VITAL_BAR_SPECS = (
    ("health", "Máu", "#e74c3c"),
    ("stamina", "Stamina", "#f1c40f"),
    ("thirst", "Nước", "#3498db"),
    ("hunger", "Food", "#e67e22"),
)


class MiniMapPanel:
    """Player-centered mini-map + vitals bars, pinned to the top-left corner."""

    def __init__(self, root: tk.Tk):
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=HUD_BG)
        self.window.geometry(f"+{HUD_MARGIN}+{HUD_MARGIN}")

        self.canvas = tk.Canvas(
            self.window, width=MINI_MAP_SIZE, height=MINI_MAP_SIZE,
            background="#1b262c", highlightthickness=1, highlightbackground="#37474f",
        )
        self.canvas.pack()

        bars_frame = tk.Frame(self.window, bg=HUD_BG)
        bars_frame.pack(fill="x", pady=(6, 4), padx=2)

        self._bar_canvases: dict[str, tk.Canvas] = {}
        self._bar_labels: dict[str, tk.Label] = {}
        for key, label, _color in VITAL_BAR_SPECS:
            row = tk.Frame(bars_frame, bg=HUD_BG)
            row.pack(fill="x", pady=1)
            tk.Label(
                row, text=label, fg="#cfd8dc", bg=HUD_BG,
                font=("Segoe UI", 8), width=7, anchor="w",
            ).pack(side="left")
            bar_canvas = tk.Canvas(row, width=120, height=14, background="#26343a", highlightthickness=0)
            bar_canvas.pack(side="left", padx=(4, 4))
            value_label = tk.Label(row, text="--", fg="#90a4ae", bg=HUD_BG, font=("Segoe UI", 8))
            value_label.pack(side="left")
            self._bar_canvases[key] = bar_canvas
            self._bar_labels[key] = value_label

        self._photo: ImageTk.PhotoImage | None = None
        self.hide()

    def show(self) -> None:
        self.window.deiconify()

    def hide(self) -> None:
        self.window.withdraw()

    def update_map(self, source_image, profile: MapProfile, x: float, y: float, heading_deg: float) -> None:
        self.canvas.delete("all")
        if source_image is None:
            return
        nx, ny = profile.to_normalized(Position(x, y, 0.0))
        width, height = source_image.size
        frac = MINI_MAP_CROP_FRACTION
        left = min(max(nx - frac / 2, 0.0), 1.0 - frac)
        top = min(max(ny - frac / 2, 0.0), 1.0 - frac)
        crop_box = (
            int(left * width), int(top * height),
            int((left + frac) * width), int((top + frac) * height),
        )
        cropped = source_image.crop(crop_box).resize((MINI_MAP_SIZE, MINI_MAP_SIZE), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(cropped)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        # Normally the player is centered. Near the source-image edges the
        # crop is clamped, so compute the marker's true position inside the
        # crop instead of blindly drawing it at the center.
        marker_x = (nx - left) / frac * MINI_MAP_SIZE
        marker_y = (ny - top) / frac * MINI_MAP_SIZE
        marker_x = max(0.0, min(float(MINI_MAP_SIZE), marker_x))
        marker_y = max(0.0, min(float(MINI_MAP_SIZE), marker_y))
        _draw_heading_polygon(self.canvas, marker_x, marker_y, heading_deg, 11, "#ff5b45")

    def update_vitals(self, status: "islepilot.IslePilotStatus") -> None:
        values = {
            "health": (status.health, status.max_health),
            "stamina": (status.stamina, status.max_stamina),
            "thirst": (status.thirst, status.max_thirst),
            "hunger": (status.hunger, status.max_hunger),
        }
        for key, _label, color in VITAL_BAR_SPECS:
            canvas = self._bar_canvases[key]
            canvas.delete("all")
            width, height = 120, 14
            canvas.create_rectangle(0, 0, width, height, fill="#26343a", outline="")
            current, maximum = values[key]
            if current is not None and maximum:
                fraction = max(0.0, min(1.0, current / maximum))
                if fraction > 0:
                    canvas.create_rectangle(0, 0, width * fraction, height, fill=color, outline="")
                self._bar_labels[key].configure(text=f"{_format_stat(current)}/{_format_stat(maximum)}")
            else:
                self._bar_labels[key].configure(text="--")

    def destroy(self) -> None:
        self.window.destroy()


class QuestPanel:
    """Prime quest checklist (up to 10 items), pinned to the top-right corner."""

    def __init__(self, root: tk.Tk):
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg=HUD_BG)
        screen_width = root.winfo_screenwidth()
        self.window.geometry(f"+{screen_width - QUEST_PANEL_WIDTH - HUD_MARGIN}+{HUD_MARGIN}")

        self.header_var = tk.StringVar(value="Prime quest")
        tk.Label(
            self.window, textvariable=self.header_var, fg="#ffd54f", bg=HUD_BG,
            font=("Segoe UI", 9, "bold"), anchor="w", wraplength=QUEST_PANEL_WIDTH - 16,
        ).pack(fill="x", padx=8, pady=(6, 4))

        self._rows: list[tuple[tk.Label, tk.Label]] = []
        for _ in range(10):
            row = tk.Frame(self.window, bg=HUD_BG)
            row.pack(fill="x", padx=8, pady=1)
            check = tk.Label(row, text="", fg="#607d8b", bg=HUD_BG, font=("Segoe UI", 9), width=2)
            check.pack(side="left")
            name = tk.Label(
                row, text="", fg="#cfd8dc", bg=HUD_BG, font=("Segoe UI", 9),
                anchor="w", wraplength=QUEST_PANEL_WIDTH - 40, justify="left",
            )
            name.pack(side="left", fill="x", expand=True)
            self._rows.append((check, name))

        self.hide()

    def show(self) -> None:
        self.window.deiconify()

    def hide(self) -> None:
        self.window.withdraw()

    def update(self, status: "islepilot.IslePilotStatus") -> None:
        self.header_var.set(f"Prime: {status.prime_done}/{status.prime_total} (cần {status.prime_required})")
        for index, (check, name) in enumerate(self._rows):
            if index < len(status.quests):
                quest = status.quests[index]
                check.configure(text="✓" if quest.done else "○", fg="#4caf50" if quest.done else "#607d8b")
                name.configure(text=islepilot.translate_quest(quest.name))
            else:
                check.configure(text="")
                name.configure(text="")

    def destroy(self) -> None:
        self.window.destroy()


class IslePilotHud:
    """Bundles the mini-map and quest panel so MapApp can treat them as one unit."""

    def __init__(self, root: tk.Tk):
        self.minimap = MiniMapPanel(root)
        self.quests = QuestPanel(root)

    def show(self) -> None:
        self.minimap.show()
        self.quests.show()

    def hide(self) -> None:
        self.minimap.hide()
        self.quests.hide()

    def update_map(self, source_image, profile: MapProfile, x: float, y: float, heading_deg: float) -> None:
        self.minimap.update_map(source_image, profile, x, y, heading_deg)

    def update_vitals(self, status: "islepilot.IslePilotStatus") -> None:
        self.minimap.update_vitals(status)

    def update_quests(self, status: "islepilot.IslePilotStatus") -> None:
        self.quests.update(status)

    def destroy(self) -> None:
        self.minimap.destroy()
        self.quests.destroy()


FOREGROUND_POLL_MS = 300


class MapApp:
    POLL_MS = 150

    def __init__(self, root: tk.Tk):
        self.root = root
        self.profiles = load_profiles()
        self.profile = self._initial_profile()
        self.current: Position | None = None
        self.previous: Position | None = None
        self.older: Position | None = None
        self.last_clipboard = ""
        self.source_image: Image.Image | None = None
        self.map_image: ImageTk.PhotoImage | None = None
        self.rendered_size: tuple[int, int] | None = None
        self.tray_icon: pystray.Icon | None = None
        self.global_escape = threading.Event()
        self.last_clipboard_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()

        self.zoom = MIN_ZOOM
        self.center_nx = 0.5
        self.center_ny = 0.5
        self._view: tuple[float, float, float, float] | None = None
        self._pan_last: tuple[int, int] | None = None
        self._pending_hq_job: str | None = None

        self._islepilot_cred_path = DATA_ROOT / "islepilot.cred"
        self._islepilot_session: islepilot.IslePilotSession | None = None
        self._islepilot_steam_id: str | None = None
        self._islepilot_heading_deg: float | None = None
        self._islepilot_online = False
        self._islepilot_logging_in = False
        self._hud: IslePilotHud | None = None
        self._settings_window: tk.Toplevel | None = None

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
        root.withdraw()
        self._poll_clipboard()
        self._poll_global_escape_event()

        saved_credentials = islepilot.load_credentials(self._islepilot_cred_path)
        if saved_credentials:
            self._islepilot_start_session(*saved_credentials)
        self._poll_hud_visibility()
        self._update_notified = False
        self.root.after(5000, self._check_for_update)

    def _initial_profile(self) -> MapProfile:
        selected = ""
        if CONFIG_PATH.exists():
            try:
                selected = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("map", "")
            except (json.JSONDecodeError, OSError):
                pass
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
        self.root.after(0, self._exit)

    def _exit(self) -> None:
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

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

    def _poll_global_escape_event(self) -> None:
        if self.global_escape.is_set():
            self.global_escape.clear()
            if self.root.state() == "normal":
                self._hide_map()
        self.root.after(20, self._poll_global_escape_event)

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

    def _select_profile(self, selected_name: str) -> None:
        self.profile = next(p for p in self.profiles if p.name == selected_name)
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps({"map": self.profile.profile_id}, indent=2), encoding="utf-8"
        )
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

        ttk.Label(frame, text="IslePilot Live (vị trí, chỉ số, nhiệm vụ realtime):").grid(
            row=4, column=0, sticky="w"
        )
        status_var = tk.StringVar(value=self._islepilot_status_text())
        ttk.Label(frame, textvariable=status_var, foreground="#4caf50").grid(
            row=5, column=0, sticky="w", pady=(2, 6)
        )

        connect_button = ttk.Button(frame)

        def refresh() -> None:
            status_var.set(self._islepilot_status_text())
            connect_button.configure(
                text="Ngắt kết nối IslePilot" if self._islepilot_connected() else "Đăng nhập Steam qua IslePilot"
            )

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

        connect_button.configure(command=on_connect_click)
        refresh()
        connect_button.grid(row=6, column=0, sticky="ew")
        ttk.Separator(frame, orient="horizontal").grid(row=7, column=0, sticky="ew", pady=14)

        ttk.Button(
            frame,
            text="Subscribe Please",
            command=lambda: webbrowser.open(YOUTUBE_URL),
        ).grid(row=8, column=0, sticky="ew")
        ttk.Button(
            frame,
            text="Join Discord",
            command=lambda: webbrowser.open(DISCORD_URL),
        ).grid(row=9, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(
            frame, text=f"The-Maps v{APP_VERSION}", foreground="#9e9e9e",
        ).grid(row=10, column=0, sticky="e", pady=(10, 0))
        # No grab_set(): Tk's modal grab on Windows is known to conflict
        # with the native caption buttons' own internal click-tracking,
        # which is what was making minimize unresponsive. Settings doesn't
        # need to block the rest of the app anyway.
        window.focus_force()

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
            messagebox.showwarning(
                "The-Maps", "Phiên IslePilot đã hết hạn. Vào Settings để đăng nhập lại."
            )

    def _apply_islepilot_status(self, status: islepilot.IslePilotStatus) -> None:
        self._islepilot_online = status.online

        # Yaw is independent of movement: process it even if x/y did not
        # change or are omitted from this particular realtime frame (a
        # yaw-only frame while spinning in place, for example).
        heading_changed = False
        if status.pos_yaw is not None:
            new_heading = self.profile.transform_yaw(status.pos_yaw)
            heading_changed = new_heading != self._islepilot_heading_deg
            self._islepilot_heading_deg = new_heading

        position: Position | None = None
        position_changed = False
        if status.pos_x is not None and status.pos_y is not None:
            # IslePilot's live-map API reports horizontal map X first and
            # vertical map Y second. The rest of this app stores The Isle's
            # clipboard/world coordinates, for which Gateway's profile has
            # swap_axes=True. Swap once here so IslePilot and clipboard
            # positions share the same coordinate convention.
            position = Position(status.pos_y, status.pos_x, status.pos_z or 0.0)
            if position != self.current:
                self.older = self.previous
                self.previous = self.current
                self.current = position
                position_changed = True

        if (position_changed or heading_changed) and self.root.state() == "normal":
            self._redraw()

        # Rotate the minimap arrow for a yaw-only frame using the last
        # known position, instead of waiting for the next x/y update.
        draw_position = position or self.current
        if self._hud is not None and draw_position is not None:
            heading = self._islepilot_heading_deg if self._islepilot_heading_deg is not None else 0.0
            self._hud.update_map(
                self.source_image, self.profile, draw_position.x, draw_position.y, heading,
            )

        if self._hud is not None:
            self._hud.update_vitals(status)
            self._hud.update_quests(status)

    def _poll_hud_visibility(self) -> None:
        if self._hud is not None:
            # Only while actually playing: IslePilot reports "online" only
            # once the player has spawned into a server session, so this
            # also keeps stale "last known dino" data (e.g. from a menu
            # screen, or a previous session on another server) from ever
            # showing as if it were live.
            if self._islepilot_online and islepilot.is_game_foreground():
                self._hud.show()
            else:
                self._hud.hide()
        self.root.after(FOREGROUND_POLL_MS, self._poll_hud_visibility)

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
        if messagebox.askyesno(
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
        self._size_map_window()

    def _poll_clipboard(self) -> None:
        try:
            sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
            if sequence != self.last_clipboard_sequence:
                self.last_clipboard_sequence = sequence
                text = self.root.clipboard_get()
                self.last_clipboard = text
                self._accept_clipboard(text)
        except tk.TclError:
            pass
        self.root.after(self.POLL_MS, self._poll_clipboard)

    def _accept_clipboard(self, text: str) -> None:
        position = parse_coordinate(text)
        if position is None:
            return
        if position == self.current:
            self._show_map()
            return
        self.older = self.previous
        self.previous = self.current
        self.current = position
        # Keep the live IslePilot yaw when connected. Clipboard updates
        # should not erase a newer realtime heading from the WebSocket.
        if not self._islepilot_connected():
            self._islepilot_heading_deg = None
        self._show_map()

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
            cache_key = (crop_box, (width, height), resample_filter)
            if self.rendered_size != cache_key:
                cropped = self.source_image.crop(crop_box)
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
        if self._view is not None:
            view_left, view_top, view_w, view_h = self._view
            x = (nx - view_left) / view_w * self._canvas_w
            y = (ny - view_top) / view_h * self._canvas_h
            return x, y
        left, top, width, height = self._placeholder_rect
        return left + nx * width, top + ny * height

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
        """Real IslePilot yaw when connected; otherwise inferred from the
        last movement between two clipboard-sourced points."""
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

    root = tk.Tk()
    try:
        MapApp(root)
    except RuntimeError as exc:
        messagebox.showerror("The-Maps", str(exc))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()

