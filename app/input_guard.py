# -*- coding: utf-8 -*-
"""May Auto-Resume touch the mouse and keyboard right now?

Three things can hold them, and each one is read separately:

1. **A person.** Windows reports when the PC last saw a mouse or keyboard event, from
   any source. Our own clicks and keystrokes are recorded in a record shared by both
   Auto-Resumes, so one program's typing never looks like a stranger to the other.
2. **Claude driving the screen.** A Code session writes every tool call to its own log,
   so the last `mcp__computer-use__…` call in a turn that has not ended yet means the
   agent is between a screenshot and a click. Stepping in then would move the window
   under its hands, which is why the wait outlasts the last click by `hold_s`.
3. **ChatGPT driving the screen.** Codex shows a small overlay saying that it is using
   the computer; its wording comes from Codex's own config file.

Reading a window disturbs nobody, so only clicking and typing consult the guard.
"""
import contextlib
import ctypes
import ctypes.wintypes
import datetime as dt
import json
import os
import re
import time

OWN_NAME = "Local\\AutoResume.OwnInput"
OWN_MARGIN_MS = 300          # input we just sent can be timestamped a moment later
SIGNAL_CACHE_S = 2.0         # logs and windows are scanned at most this often

CU_TOOL = b"mcp__computer-use__"
CU_END = re.compile(rb'"stop_reason"\s*:\s*"end_turn"')
CU_TS = re.compile(rb'"timestamp"\s*:\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})')
LOG_WINDOW_S = 300           # a computer-use session in progress writes constantly
TAIL_BYTES = 256 * 1024
MAX_FILES = 40

OVERLAY_EXES = ("claude.exe", "chatgpt.exe", "codex.exe")
OVERLAY_FALLBACK = ("is using your computer", "używa twojego komputera")
OVERLAY_MAX_AREA = 0.4       # of the primary screen: the overlay is a small strip
OVERLAY_NODES = 200          # a shallow read is enough to see its wording

user32 = ctypes.windll.user32
# Our own WinDLL handle, so the prototypes below never reach another module's calls.
# On 64-bit Windows a handle or pointer returned without a declared restype is
# truncated to 32 bits, and reading through it takes the process down.
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileMappingW.restype = ctypes.wintypes.HANDLE
kernel32.CreateFileMappingW.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_void_p,
                                        ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
                                        ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR]
kernel32.MapViewOfFile.restype = ctypes.c_void_p
kernel32.MapViewOfFile.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
                                   ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_size_t]
kernel32.GetTickCount64.restype = ctypes.c_ulonglong
kernel32.GetTickCount.restype = ctypes.wintypes.DWORD
kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
INVALID_HANDLE = ctypes.wintypes.HANDLE(-1)
FILE_MAP_ALL_ACCESS, PAGE_READWRITE = 0xF001F, 0x4


class InputPaused(RuntimeError):
    """Someone else is using the mouse; the action was not performed."""


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.UINT), ("dwTime", ctypes.wintypes.DWORD)]


def last_input_age_ms():
    """Milliseconds since the last mouse or keyboard event, whoever sent it."""
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0                  # unknown: treat as "just now" and stay out of the way
    return (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


class OwnInputClock:
    """When this PC last received input that WE sent, shared by both Auto-Resumes."""

    def __init__(self, name=OWN_NAME):
        self.view = None
        try:
            handle = kernel32.CreateFileMappingW(INVALID_HANDLE, None, PAGE_READWRITE, 0, 8, name)
            if handle:
                address = kernel32.MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, 8)
                if address:
                    self.view = ctypes.cast(address, ctypes.POINTER(ctypes.c_ulonglong))
        except Exception:
            self.view = None      # no shared record: own input is simply unknown

    def mark(self, ahead_ms=0):
        """Input until `ahead_ms` from now is ours (0: input up to now)."""
        if self.view is not None:
            self.view[0] = kernel32.GetTickCount64() + ahead_ms

    def age_ms(self):
        if self.view is None or not self.view[0]:
            return None
        return max(0, kernel32.GetTickCount64() - self.view[0])


# --------------------------------------------------------- Claude's computer use


def claude_projects_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


class ClaudeComputerUse:
    """Is a Claude Code session in the middle of driving the screen?

    Read-only, like quota_log: only the tails of logs written in the last few minutes,
    only the two records that matter, and nothing is kept but the time of the last
    computer-use call. (Its time, not its age: a log that stays unchanged while the agent
    runs a long command must not keep the hold on for ever.)
    """

    def __init__(self, root=None):
        self.root = root if root is not None else claude_projects_root()
        self._cache = {}          # path -> ((mtime_ns, size), epoch seconds or None)

    def active(self, hold_s):
        newest, cache = None, {}
        for path, stat in self._recent_logs():
            key = (stat.st_mtime_ns, stat.st_size)
            cached = self._cache.get(path)
            if cached is None or cached[0] != key:
                cached = (key, self._scan(path, stat.st_size))
            cache[path] = cached
            when = cached[1]
            if when is not None and (newest is None or when > newest):
                newest = when
        self._cache = cache
        return newest is not None and time.time() - newest <= hold_s

    def _recent_logs(self):
        cutoff = time.time() - LOG_WINDOW_S
        logs = []
        try:
            projects = [entry.path for entry in os.scandir(self.root) if entry.is_dir()]
        except OSError:
            return []
        for project in projects:
            try:
                for entry in os.scandir(project):
                    if entry.is_file() and entry.name.endswith(".jsonl"):
                        stat = entry.stat()
                        if stat.st_mtime >= cutoff:
                            logs.append((entry.path, stat))
            except OSError:
                continue
        logs.sort(key=lambda item: item[1].st_mtime, reverse=True)
        return logs[:MAX_FILES]

    def _scan(self, path, size):
        """When (epoch seconds) the last computer-use call of an unfinished turn was made,
        or None."""
        start = max(0, size - TAIL_BYTES)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(TAIL_BYTES)
        except OSError:
            return None
        lines = data.split(b"\n")
        if start:
            lines = lines[1:]     # the first line is cut mid-record
        for raw in reversed(lines):
            if CU_TOOL not in raw and not CU_END.search(raw):
                continue          # the cheap test: most records concern neither
            message = _assistant_message(raw)
            if message is None:
                continue          # the tools only named: MCP instructions, a file read, a quote
            if message.get("stop_reason") == "end_turn":
                return None       # the turn ended: the mouse is free again
            if any(isinstance(part, dict) and part.get("type") == "tool_use"
                   and str(part.get("name", "")).startswith(CU_TOOL.decode())
                   for part in message.get("content") or ()):
                return self._when(raw)
        return None

    @staticmethod
    def _when(raw):
        match = CU_TS.search(raw)
        if not match:
            return time.time()    # no timestamp: treat it as "just now"
        try:
            when = dt.datetime.strptime(match[1].decode(), "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=dt.timezone.utc)
        except ValueError:
            return time.time()
        return min(time.time(), when.timestamp())


def _assistant_message(raw):
    """The message of an assistant record, or None for any other line. Only the agent's
    own records say what it called; instructions and tool results may merely name a tool."""
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return None
    message = record.get("message")
    return message if isinstance(message, dict) else None


# ------------------------------------------------------- ChatGPT's computer use


def overlay_phrases(codex_home=None):
    """Lower-case wordings of the "… is using your computer" overlay.

    Codex ships its own translation in ~/.codex/computer-use/config.json, so the
    overlay is recognised in whatever language the app speaks.
    """
    phrases = set(OVERLAY_FALLBACK)
    base = codex_home or os.environ.get("CODEX_HOME") or os.path.join(
        os.path.expanduser("~"), ".codex")
    try:
        with open(os.path.join(base, "computer-use", "config.json"), encoding="utf-8") as f:
            text = (json.load(f).get("strings") or {}).get("usingComputer")
        if isinstance(text, str) and text.strip():
            phrases.add(text.strip().casefold())
    except (OSError, ValueError, AttributeError):
        pass
    return tuple(sorted(phrases))


def _exe_of(hwnd):
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


def _overlay_candidates(skip_hwnd=None):
    """Small visible windows of Claude, ChatGPT or Codex — never their main window."""
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    budget = OVERLAY_MAX_AREA * max(1, width * height)
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if hwnd == skip_hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area <= 0 or area > budget:
            return True
        if _exe_of(hwnd) in OVERLAY_EXES:
            found.append(hwnd)
        return True

    try:
        user32.EnumWindows(visit, 0)
    except Exception:
        return []
    return found


def _shallow_text(hwnd):
    """The first control names inside a window, enough to read an overlay's wording."""
    try:
        import uiautomation as auto
        control = auto.ControlFromHandle(hwnd)
        if control is None:
            return ""
        names = []
        for ctrl, _depth in auto.WalkControl(control, includeTop=True, maxDepth=6):
            try:
                name = (ctrl.Name or "").strip()
            except Exception:
                continue
            if name:
                names.append(name)
            if len(names) >= OVERLAY_NODES:
                break
        return " ".join(names).casefold()
    except Exception:
        return ""


def overlay_visible(skip_hwnd=None, phrases=None):
    """True when one of the apps shows its "… is using your computer" overlay."""
    phrases = phrases if phrases is not None else overlay_phrases()
    for hwnd in _overlay_candidates(skip_hwnd):
        title = _window_title(hwnd).casefold()
        if any(phrase in title for phrase in phrases):
            return True
        if any(phrase in _shallow_text(hwnd) for phrase in phrases):
            return True
    return False


# ------------------------------------------------------------------- the guard


class InputGuard:
    def __init__(self, worker, *, last_input=None, own=None, cu=None, overlay=None):
        self.worker = worker
        self._last_input = last_input or last_input_age_ms
        self.own = own if own is not None else OwnInputClock()
        self.cu = cu if cu is not None else ClaudeComputerUse()
        self.overlay = overlay if overlay is not None else overlay_visible
        self._ignore_user_until = 0.0
        self._cached = (0.0, None)

    def _cfg(self, key, default):
        value = self.worker.cfg.get(key, default)
        return default if value is None else value

    def enabled(self):
        return bool(self._cfg("pause_on_foreign_input", True))

    def mark_own_input(self):
        """Called right after every click, keystroke and cursor move we make."""
        self.own.mark()

    @contextlib.contextmanager
    def sending(self, seconds=1.0):
        """Around a click or keystrokes. The other Auto-Resume reads the shared record at
        any moment, also while our input is on its way (a click returns only half a second
        after it went out), so the input is ours from before it is sent until after."""
        self.own.mark(ahead_ms=int(seconds * 1000))
        try:
            yield
        finally:
            self.own.mark()

    def foreign_input_age_s(self):
        """Seconds since the newest input that was not ours, or None when it was ours."""
        age = self._last_input()
        own = self.own.age_ms()
        if own is not None and age >= own - OWN_MARGIN_MS:
            return None
        return age / 1000.0

    def ignore_user_for(self, seconds):
        """A command the user just clicked in our own window must not wait for calm."""
        self._ignore_user_until = time.monotonic() + seconds

    def user_busy(self):
        """The cheap check, made again right before every click and every keystroke."""
        if not self.enabled() or time.monotonic() < self._ignore_user_until:
            return False
        age = self.foreign_input_age_s()
        return age is not None and age < self._cfg("foreign_input_quiet_s", 30)

    def pause_reason(self):
        """None, "user", "claude_cu" or "chatgpt_cu" — why we must not act now."""
        if not self.enabled():
            return None
        if self.user_busy():
            return "user"
        fresh, reason = self._cached
        if time.monotonic() < fresh:
            return reason
        reason = None
        try:
            if self.cu.active(self._cfg("computer_use_hold_s", 120)):
                reason = "claude_cu"
            elif self.overlay(skip_hwnd=getattr(self.worker, "hwnd", None)):
                reason = "chatgpt_cu"
        except Exception:
            reason = None         # an unreadable log or window must not stop the watch
        self._cached = (time.monotonic() + SIGNAL_CACHE_S, reason)
        return reason
