# -*- coding: utf-8 -*-
"""Auto-Resume launcher: pick which auto resume to use.

Main.bat opens this window with three tiles: ChatGPT, Claude, and "My brain has
no wrinkles left" (both). A tile starts the matching Auto-Resume as its own program
and closes the picker. Both programs can run side by side: they take turns at the
keyboard (input_lock.py).

The tile pictures ship in assets/, so every copy of the program shows them, whether or
not the apps (or Pillow) are installed. tools/make_app_icons.py drew the ChatGPT and
Claude ones from the apps' own icons; their logos only name the app a tile starts.
"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import tkinter as tk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(APP_DIR, "assets")

HEADLINE = "Pick which auto resume you want to use"
TILES = (
    dict(key="chatgpt", title="ChatGPT", subtitle="", scripts=("chatgpt_auto_continue.py",),
         apps=("chatgpt",)),
    dict(key="claude", title="Claude", subtitle="", scripts=("claude_auto_continue.py",),
         apps=("claude",)),
    dict(key="both", title="My brain has no wrinkles left", subtitle="(launch both and use both)",
         scripts=("claude_auto_continue.py", "chatgpt_auto_continue.py"), apps=("claude", "chatgpt")),
)
APP_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT"}
AUTO_RESUME_SIZE = (860, 960)     # the Auto-Resume window (claude_auto_continue.App)

# Same night palette as the Auto-Resume windows.
THEME = {"bg": "#14161B", "panel": "#1C1F26", "panel_hi": "#242935", "border": "#2A2E38",
         "text": "#E9E4D6", "muted": "#8B92A0", "amber": "#E0A458", "red": "#D98080"}

# ------------------------------------------------------------------- icons

ICONS = {"chatgpt": "chatgpt.png", "claude": "claude.png", "both": "brain.png"}


def icon_path(key):
    """The tile's picture, one of the program's own files."""
    return os.path.join(ASSETS, ICONS[key])


def load_icon(master, path, size):
    """A Tk image of about `size` px; smooth with Pillow, plain Tk otherwise."""
    try:
        from PIL import Image, ImageTk
        img = Image.open(path).convert("RGBA")
        img.thumbnail((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img, master=master)
    except ImportError:
        pass
    except Exception:
        return None
    try:
        photo = tk.PhotoImage(master=master, file=path)
    except tk.TclError:
        return None
    factor = max(1, round(max(photo.width(), photo.height()) / size))
    return photo.subsample(factor) if factor > 1 else photo


# ---------------------------------------------------------------- launching


def gui_python():
    """pythonw.exe next to the running interpreter, so no console window opens."""
    folder, name = os.path.split(sys.executable)
    if name.lower() == "python.exe" and os.path.isfile(os.path.join(folder, "pythonw.exe")):
        return os.path.join(folder, "pythonw.exe")
    return sys.executable


def launch(script, args=()):
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen([gui_python(), os.path.join(APP_DIR, script), *args],
                            cwd=APP_DIR, creationflags=flags, close_fds=True)


def work_area():
    """(left, top, right, bottom) of the primary screen without the taskbar."""
    rect = ctypes.wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):   # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right, rect.bottom
    user32 = ctypes.windll.user32
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def side_by_side(area, size=AUTO_RESUME_SIZE, gap=16):
    """Tk geometry strings placing two Auto-Resume windows next to each other."""
    left, top, right, bottom = area
    width, height = size
    y = top + max(0, (bottom - top - height) // 2)
    middle = (left + right) // 2
    x_left = max(left, middle - gap // 2 - width)
    x_right = max(left, min(right - width, middle + gap // 2))
    if x_right < x_left + 60:                 # too narrow: cascade instead
        x_right = x_left + 60
    return f"+{x_left}+{y}", f"+{x_right}+{y}"


TITLES = {"claude_auto_continue.py": "Claude Auto-Resume", "chatgpt_auto_continue.py": "ChatGPT Auto-Resume"}


def running_window(script):
    """The window of the program when it is already open, else 0."""
    return ctypes.windll.user32.FindWindowW("TkTopLevel", TITLES[script])


def bring_forward(hwnd):
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                           # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def launch_tile(tile):
    """Start the tile's program(s); one that is already open is brought forward instead,
    so a second click never opens a second copy watching the same window. Both at once:
    Claude on the left, ChatGPT on the right."""
    scripts = tile["scripts"]
    places = side_by_side(work_area()) if len(scripts) > 1 else (None,)
    started = []
    for script, place in zip(scripts, places):
        hwnd = running_window(script)
        if hwnd:
            bring_forward(hwnd)
        else:
            started.append(launch(script, ("--geometry", place) if place else ()))
    return started


# ------------------------------------------------ keeping both apps readable

APP_EXES = {"claude": ("claude.exe",), "chatgpt": ("chatgpt.exe", "codex.exe")}


def _exe(hwnd):
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)     # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        return os.path.basename(buf.value).lower() if kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", ctypes.wintypes.UINT), ("flags", ctypes.wintypes.UINT),
                ("showCmd", ctypes.wintypes.UINT), ("ptMinPosition", ctypes.wintypes.POINT),
                ("ptMaxPosition", ctypes.wintypes.POINT), ("rcNormalPosition", ctypes.wintypes.RECT)]


def _normal_rect(hwnd):
    """Where the window sits when it is not minimized."""
    placement = _WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(placement)
    ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
    return placement.rcNormalPosition


def _app_windows(include_minimized):
    """(app, hwnd, area, click_through) of the visible windows of Claude and ChatGPT; a
    minimized one is measured by its normal size."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def visit(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        minimized = user32.IsIconic(hwnd)
        if (cls.value != "Chrome_WidgetWin_1" or not user32.IsWindowVisible(hwnd)
                or (minimized and not include_minimized)):
            return True
        exe = _exe(hwnd)
        for app, exes in APP_EXES.items():
            if exe in exes:
                r = _normal_rect(hwnd) if minimized else ctypes.wintypes.RECT()
                if not minimized:
                    user32.GetWindowRect(hwnd, ctypes.byref(r))
                through = bool(user32.GetWindowLongW(hwnd, -20) & 0x20)    # WS_EX_TRANSPARENT
                found.append((app, hwnd, (r.right - r.left) * (r.bottom - r.top), through))
        return True

    user32.EnumWindows(visit, 0)
    return found


def pick_main_windows(windows):
    """The largest window of each app. A click-through window is an overlay the app draws
    over the screen (Claude's while it drives the computer), never the window to watch."""
    best = {}
    for app, hwnd, area, through in windows:
        if not through and area > best.get(app, (0, None))[0]:
            best[app] = (area, hwnd)
    return {app: hwnd for app, (_, hwnd) in best.items()}


def main_windows(include_minimized=False):
    """{"claude": hwnd, "chatgpt": hwnd}: the main window of each app that is running."""
    return pick_main_windows(_app_windows(include_minimized))


def is_minimized(hwnd):
    return bool(ctypes.windll.user32.IsIconic(hwnd))


def show_without_focus(hwnd):
    ctypes.windll.user32.ShowWindow(hwnd, 4)                 # SW_SHOWNOACTIVATE


def show_apps(apps):
    """Watching an app needs its window on screen: a minimized one shows screen readers an
    empty page. Bring the minimized ones back without taking the focus; returns which."""
    shown = []
    for app, hwnd in main_windows(include_minimized=True).items():
        if app in apps and is_minimized(hwnd):
            show_without_focus(hwnd)
            shown.append(app)
    return shown


def _rect(hwnd):
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def covers(outer, inner, slack=16):
    """True when `outer` hides all of `inner` (both (l, t, r, b))."""
    return (outer[0] <= inner[0] + slack and outer[1] <= inner[1] + slack
            and outer[2] >= inner[2] - slack and outer[3] >= inner[3] - slack)


def arrange_apps():
    """Watching both needs both windows readable, and a window that is completely
    covered shows an empty page to screen readers. Only when one app window hides
    the other (e.g. both maximized), put Claude on the left half of the screen and
    ChatGPT on the right. Returns True when windows were moved."""
    windows = main_windows()
    if len(windows) != 2:
        return False
    claude, chatgpt = windows["claude"], windows["chatgpt"]
    a, b = _rect(claude), _rect(chatgpt)
    if not (covers(a, b) or covers(b, a)):
        return False
    user32 = ctypes.windll.user32
    left, top, right, bottom = work_area()
    half = (right - left) // 2
    for hwnd, x in ((claude, left), (chatgpt, left + half)):
        user32.ShowWindow(hwnd, 9)                                   # SW_RESTORE (un-maximize)
        user32.SetWindowPos(hwnd, None, x, top, half, bottom - top, 0x0004 | 0x0010)   # NOZORDER|NOACTIVATE
    return True


# ------------------------------------------------------------------- window


class Launcher(tk.Tk):
    ICON_PX = 150

    def __init__(self):
        super().__init__()
        self.title("Auto-Resume")
        self.configure(bg=THEME["bg"])
        self.resizable(False, False)
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.font = "Segoe UI Variable Text" if "Segoe UI Variable Text" in self.tk.call("font", "families") \
            else "Segoe UI"
        self.images = {}
        self.tiles = []
        self.busy = False
        self._build()
        self._dark_titlebar()
        self._center()
        self.bind("<Escape>", lambda _e: self.destroy())
        for number, _ in enumerate(TILES, start=1):
            self.bind(str(number), lambda _e, i=number - 1: self._activate(i))
        self.bind("<Left>", lambda _e: self._move_focus(-1))
        self.bind("<Right>", lambda _e: self._move_focus(1))
        self.after(50, lambda: self.tiles[0]["frame"].focus_set())

    def px(self, value):
        return int(value * self.scale)

    # -------------------------------------------------------------- layout
    def _build(self):
        t = THEME
        outer = tk.Frame(self, bg=t["bg"], padx=self.px(36), pady=self.px(28))
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="AUTO-RESUME", bg=t["bg"], fg=t["muted"],
                 font=(self.font, 9, "bold")).pack()
        tk.Label(outer, text=HEADLINE, bg=t["bg"], fg=t["text"],
                 font=(self.font, 20, "bold")).pack(pady=(self.px(4), self.px(22)))
        row = tk.Frame(outer, bg=t["bg"])
        row.pack()
        for index, tile in enumerate(TILES):
            self._tile(row, index, tile).grid(row=0, column=index, padx=self.px(10))
        self.status = tk.Label(outer, text="Click a tile, or press 1, 2 or 3.", bg=t["bg"], fg=t["muted"],
                               font=(self.font, 9))
        self.status.pack(pady=(self.px(20), 0))

    def _tile(self, parent, index, tile):
        t = THEME
        width, height = self.px(290), self.px(330)
        frame = tk.Frame(parent, width=width, height=height, bg=t["panel"], cursor="hand2", takefocus=1,
                         highlightthickness=2, highlightbackground=t["border"], highlightcolor=t["amber"])
        frame.grid_propagate(False)
        frame.pack_propagate(False)
        inner = tk.Frame(frame, bg=t["panel"])
        inner.place(relx=0.5, rely=0.5, anchor="center")
        # Two lines for every title, so the pictures line up across the tiles.
        title = tk.Label(inner, text=tile["title"], bg=t["panel"], fg=t["text"], wraplength=self.px(250),
                         justify="center", height=2, font=(self.font, 16, "bold"))
        title.pack()
        subtitle = tk.Label(inner, text=tile["subtitle"] or " ", bg=t["panel"], fg=t["muted"],
                            font=(self.font, 10))
        subtitle.pack(pady=(self.px(2), self.px(14)))
        icon = self._icon(inner, tile["key"])
        icon.pack()
        parts = [frame, inner, title, subtitle, icon]
        entry = dict(frame=frame, parts=parts, title=title, subtitle=subtitle, tile=tile)
        self.tiles.append(entry)
        for widget in parts:
            widget.bind("<Enter>", lambda _e, e=entry: self._hover(e, True))
            widget.bind("<Leave>", lambda _e, e=entry: self._hover(e, False))
            widget.bind("<Button-1>", lambda _e, i=index: self._activate(i))
        frame.bind("<Return>", lambda _e, i=index: self._activate(i))
        frame.bind("<space>", lambda _e, i=index: self._activate(i))
        frame.bind("<FocusIn>", lambda _e, e=entry: self._hover(e, True))
        frame.bind("<FocusOut>", lambda _e, e=entry: self._hover(e, False))
        return frame

    def _icon(self, parent, key):
        """The tile's picture, from the program's own assets."""
        size = self.px(self.ICON_PX)
        path = icon_path(key)
        image = load_icon(self, path, size) if os.path.isfile(path) else None
        if image is not None:
            self.images[key] = image       # Tk drops images nobody references
            return tk.Label(parent, image=image, bg=THEME["panel"])
        # The picture is missing or unreadable: a plain monogram instead.
        canvas = tk.Canvas(parent, width=size, height=size, bg=THEME["panel"], highlightthickness=0)
        canvas.create_oval(4, 4, size - 4, size - 4, outline=THEME["muted"], width=2)
        canvas.create_text(size / 2, size / 2, text={"chatgpt": "GPT", "claude": "C"}.get(key, "?"),
                           fill=THEME["text"], font=(self.font, 28, "bold"))
        return canvas

    def _hover(self, entry, on):
        t = THEME
        if self.busy:
            return
        bg = t["panel_hi"] if on else t["panel"]
        entry["frame"].configure(highlightbackground=t["amber"] if on else t["border"])
        for widget in entry["parts"]:
            widget.configure(bg=bg)

    def _move_focus(self, step):
        focused = self.focus_get()
        index = next((i for i, e in enumerate(self.tiles) if e["frame"] is focused), 0)
        self.tiles[(index + step) % len(self.tiles)]["frame"].focus_set()

    # ------------------------------------------------------------- actions
    def _activate(self, index):
        if self.busy:
            return
        entry = self.tiles[index]
        tile = entry["tile"]
        self.busy = True
        entry["subtitle"].configure(text="Starting…", fg=THEME["amber"])
        self.status.configure(text="Starting " + ("both Auto-Resumes" if tile["key"] == "both"
                                                  else tile["title"] + " Auto-Resume") + "…", fg=THEME["muted"])
        self.update_idletasks()
        note = ""
        try:
            shown = show_apps(tile["apps"])
            if tile["key"] == "both" and arrange_apps():
                note = "Claude and ChatGPT were placed side by side, so both can be watched."
            elif len(shown) == 1:
                note = f"{APP_NAMES[shown[0]]} was minimized and is back on screen, so it can be watched."
            elif shown:
                note = "Claude and ChatGPT were minimized and are back on screen, so both can be watched."
            launch_tile(tile)
        except Exception as exc:     # e.g. Python or a script missing
            self.busy = False
            entry["subtitle"].configure(text=tile["subtitle"] or " ", fg=THEME["muted"])
            self.status.configure(text=f"Couldn’t start it: {exc}", fg=THEME["red"])
            return
        if note:
            self.status.configure(text=note, fg=THEME["text"])
        self.after(2200 if note else 700, self.destroy)

    # --------------------------------------------------------------- chrome
    def _dark_titlebar(self):
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                                             ctypes.sizeof(value)) == 0:
                    break
        except Exception:
            pass

    def _center(self):
        self.update_idletasks()
        left, top, right, bottom = work_area()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 3)
        self.geometry(f"+{x}+{y}")


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    Launcher().mainloop()


if __name__ == "__main__":
    main()
