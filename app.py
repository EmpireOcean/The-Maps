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
from PIL import Image, ImageTk


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
                )
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if not profiles:
        raise RuntimeError("Không tìm thấy map profile hợp lệ trong thư mục maps.")
    return profiles


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

        root.title("The-Maps")
        if APP_ICON_ICO.exists():
            root.iconbitmap(default=str(APP_ICON_ICO))
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.bind("<Escape>", self._hide_map)

        self._build_ui()
        self._load_map_image()
        self._redraw()
        self._start_tray()
        threading.Thread(target=self._keyboard_hook_loop, daemon=True).start()
        root.withdraw()
        self._poll_clipboard()
        self._poll_global_escape_event()

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
            pystray.MenuItem("Exit The-Maps", self._tray_exit, default=True),
            pystray.MenuItem("Settings", self._tray_open_settings),
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
                if event.vk_code == 0x1B:
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
        window = tk.Toplevel(self.root)
        window.title("The-Maps Settings")
        window.resizable(False, False)
        window.attributes("-topmost", True)
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
            window.destroy()

        ttk.Button(frame, text="Lưu", command=save).grid(row=2, column=0, sticky="e")
        ttk.Separator(frame, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=14)
        ttk.Button(
            frame,
            text="Subscribe Please",
            command=lambda: webbrowser.open(YOUTUBE_URL),
        ).grid(row=4, column=0, sticky="ew")
        ttk.Button(
            frame,
            text="Join Discord",
            command=lambda: webbrowser.open(DISCORD_URL),
        ).grid(row=5, column=0, sticky="ew", pady=(8, 0))
        window.grab_set()
        window.focus_force()

    def _load_map_image(self) -> None:
        self.source_image = None
        self.map_image = None
        self.rendered_size = None
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
        self._show_map()

    def _redraw(self) -> None:
        canvas = self.canvas
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        canvas.delete("all")

        if self.source_image:
            source_width, source_height = self.source_image.size
            scale = min(width / source_width, height / source_height)
            map_width = max(1, int(source_width * scale))
            map_height = max(1, int(source_height * scale))
            size = (map_width, map_height)
            if self.rendered_size != size:
                resized = self.source_image.resize(size, Image.Resampling.LANCZOS)
                self.map_image = ImageTk.PhotoImage(resized)
                self.rendered_size = size
            left = (width - map_width) / 2
            top = (height - map_height) / 2
            canvas.create_image(width / 2, height / 2, image=self.map_image)
        else:
            margin = 24
            left, top = margin, margin
            map_width, map_height = width - margin * 2, height - margin * 2
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

        self._draw_history_path(left, top, map_width, map_height)
        if self.older:
            self._draw_marker(self.older, left, top, map_width, map_height, 5, "#71838b")
        if self.previous:
            self._draw_marker(self.previous, left, top, map_width, map_height, 8, "#55a8c9")
        if self.current:
            self._draw_marker(self.current, left, top, map_width, map_height, 12, "#ff5b45")
        if self.current and self.previous:
            self._draw_movement_direction(left, top, map_width, map_height)

    def _pixel(self, position: Position, left: float, top: float, width: float, height: float):
        nx, ny = self.profile.to_normalized(position)
        return left + nx * width, top + ny * height

    def _draw_marker(self, position, left, top, width, height, radius, color):
        x, y = self._pixel(position, left, top, width, height)
        self.canvas.create_oval(
            x - radius, y - radius, x + radius, y + radius,
            fill=color, outline="white", width=2,
        )

    def _draw_history_path(self, left, top, width, height):
        positions = [position for position in (self.older, self.previous, self.current) if position]
        if len(positions) < 2:
            return
        points: list[float] = []
        for position in positions:
            x, y = self._pixel(position, left, top, width, height)
            points.extend((x, y))
        self.canvas.create_line(
            *points,
            fill="#d4e3e8",
            width=2,
            dash=(3, 7),
        )

    def _draw_movement_direction(self, left, top, width, height):
        x1, y1 = self._pixel(self.previous, left, top, width, height)
        x2, y2 = self._pixel(self.current, left, top, width, height)
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 2:
            return
        ux, uy = dx / length, dy / length
        self.canvas.create_line(
            x2, y2, x2 + ux * 34, y2 + uy * 34,
            fill="#ffd54f", width=4, arrow="last",
        )


def main() -> None:
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

