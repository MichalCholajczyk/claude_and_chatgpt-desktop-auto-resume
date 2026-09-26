# -*- coding: utf-8 -*-
"""
Claude Auto-Resume — auto-continue for Claude Desktop (Windows 11).

Watches the Claude app window via UI Automation, detects the usage-limit
message (5-hour / weekly), parses the reset time and, one minute after the
reset, types the message to send (DEFAULT_MESSAGE unless changed) into the
chat box and presses Enter.

UI is bilingual (English / Polish), default English.

Requires: Python 3.10+, package `uiautomation` (pip install uiautomation).
"""

import collections
import ctypes
import ctypes.wintypes
import datetime as dt
import json
import math
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
sys.modules.setdefault("claude_auto_continue", sys.modules[__name__])
from handover import parse_threshold
from input_guard import InputGuard
from input_lock import input_lock, worker_cancelled
from strings import STRINGS, Text
from session_automation import SessionEngine, WindowHidden

try:
    import uiautomation as auto
except ImportError:
    ctypes.windll.user32.MessageBoxW(
        0,
        "Missing package 'uiautomation'.\n\nInstall it:  py -m pip install uiautomation",
        "Claude Auto-Resume", 0x10)
    sys.exit(1)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "auto_continue_config.json")
LOG_PATH = os.path.join(APP_DIR, "auto_continue.log")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------------------------------------------------------- configuration

# What is typed into a conversation once its limit has reset. The agent gets told why it
# stopped, so it picks the work up instead of asking what "continue" refers to.
DEFAULT_MESSAGE = ("I hit my usage limit while you were working, but it has reset now. "
                   "Please continue from where you left off if possible autonomously.")

DEFAULT_CONFIG = {
    "language": "en",               # "en" or "pl"
    "scan_interval_s": 20,          # how often to scan the window (seconds)
    "send_delay_after_reset_s": 60, # seconds after reset to send the message
    "message": DEFAULT_MESSAGE,     # what to type into the chat
    "message_box_lines": 4,         # height of the message box, in text lines
    "auto_send": True,              # False = only alert, do not send
    "keep_awake": True,             # keep Windows from sleeping
    "start_delay_s": 10,            # countdown before the app starts driving Claude
    "watch_scope": "open",          # open panes or explicitly selected chats
    "selected_chats": [],
    "prefer_try_again": False,
    "retry_api_errors": True,
    "auto_approach": False,
    "auto_permissions": False,
    "api_retry_wait_s": 30,
    "verify_delay_s": 30,
    "max_retries": 6,               # retries while the limit is still active
    "retry_wait_s": 600,            # retry spacing when reset time is unknown
    "handover_enabled": False,       # hand the work over when the context fills up
    "handover_threshold": "700k",    # "700k", "0.7M", "700000" or "70%"
    "plan_folder": "",               # where the next sections of the project are written
    "handover_request_text": "",     # empty = the default text from the translations
    "handover_continue_text": "",
    "handover_timeout_min": 30,      # how long a handover may take before it needs help
    "pause_on_foreign_input": True,  # hold still while someone else uses the mouse
    "foreign_input_quiet_s": 30,     # calm needed before acting again
    "computer_use_hold_s": 120,      # ...after an agent's last computer-use call
    "limit_threshold_pct": 100,     # 5-hour limit % that counts as "hit"
    "panel_backoff_s": 300,         # min spacing between usage-panel opens while
                                    # the plan meter is maxed out by a weekly limit
}


# Which conversations to watch is decided anew on every run: never loaded, never saved.
RUNTIME_ONLY = ("watch_scope", "selected_chats")


def load_config(path=None, defaults=None):
    """Settings from the file on top of the defaults. ChatGPT Auto-Resume passes its
    own defaults, which differ where Codex differs (e.g. a smaller context window)."""
    defaults = defaults or DEFAULT_CONFIG
    cfg = dict(defaults)
    try:
        with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError, TypeError):
        pass
    if cfg.get("language") not in ("en", "pl"):
        cfg["language"] = "en"
    cfg["watch_scope"], cfg["selected_chats"] = "open", []
    for field, minimum, maximum in (("scan_interval_s", 5, 300), ("api_retry_wait_s", 5, 900),
                                    ("max_retries", 1, 20), ("retry_wait_s", 30, 86400),
                                    ("verify_delay_s", 5, 300), ("send_delay_after_reset_s", 0, 3600),
                                    ("start_delay_s", 0, 120), ("foreign_input_quiet_s", 5, 300),
                                    ("computer_use_hold_s", 30, 900), ("handover_timeout_min", 5, 240)):
        try:
            cfg[field] = min(maximum, max(minimum, int(cfg[field])))
        except (TypeError, ValueError):
            cfg[field] = defaults[field]
    return cfg


def save_config(cfg, path=None):
    data = {key: value for key, value in cfg.items() if key not in RUNTIME_ONLY}
    try:
        with open(path or CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

# ----------------------------------------------------------------- translations
# The tables live in strings.py, which both Auto-Resumes share.


def tr(lang, key, table=None, **kw):
    """Translate a key; fall back to English, then to the raw key. `table` is a
    product's own string table (ChatGPT Auto-Resume); Claude's by default. Values that
    are Texts (translated parts, e.g. a chat's status in a log line) are translated
    again into `lang`, so a whole line changes language, not just its frame."""
    table = table or STRINGS
    template = table.get(lang, table["en"]).get(key)
    if template is None:
        template = table["en"].get(key, key)
    kw = {name: _retranslate(lang, value, table) for name, value in kw.items()}
    try:
        return template.format(**kw)
    except (KeyError, IndexError, ValueError):
        return template


def _retranslate(lang, value, table):
    if not isinstance(value, Text):
        return value
    if value.key is None:                    # Text.joined: a list of texts
        return value.kw["sep"].join(str(_retranslate(lang, part, table)) for part in value.kw["parts"])
    return tr(lang, value.key, table, **value.kw)

# ------------------------------------------------------------------- detection

# The "Usage limit reached" notice Claude shows next to the chat box (and as a
# notification card) while the session is hard-blocked. Unlike the usage-meter
# percentages, which can go stale or read below 100% during a block, this text
# is only rendered while the block is active — treat it as authoritative.
# Patterns are deliberately narrow so meter/panel labels ("5-hour limit",
# "Resets in 1 hr") can never match.
BANNER_LIMIT_PATTERNS = [
    r"usage\s+limit\s+reached",
    r"(?:session|weekly)\s+limit\s+reached",
    r"reached\s+your\s+(?:usage|session|weekly)\s+limit",
    r"hit\s+your\s+(?:usage|session|weekly)\s+limit",
    r"out\s+of\s+usage",
    r"osi[ąa]gni[ęe]to\s+limit",
    r"limit\s+u[żz]ycia\s+(?:zosta[łl]\s+)?osi[ąa]gni[ęe]ty",
    r"limit\s+(?:zosta[łl]\s+)?osi[ąa]gni[ęe]ty",
]

WEEKDAY_WORD = (r"(?:mon|tue|wed|thu|fri|sat|sun|pon|wt|śr|sr|czw|pi[ąa]t|pt|sob|ndz|nie)"
                r"[a-ząćęłńóśźż]*\.?")
MONTH_WORD = (r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|sty|lut|kwi|maj|cze|lip|sie|"
              r"wrz|paź|paz|lis|gru)[a-ząćęłńóśźż]*\.?")
CLOCK = r"(?P<hh>\d{1,2})(?::(?P<mm>\d{2}))?\s*(?P<ampm>am|pm)?"

# e.g. "Resets Mon, Jul 13, 6:00 PM" / "resets 9:30am (Europe/Warsaw)" /
# "It resets Monday at 6:00 PM" / "resets at 6:00 PM on Thursday"
# (also matches Claude's Polish UI wording, e.g. the "resetuje ... o 15:00" form)
RE_RESET_ABS = re.compile(
    r"reset(?:s|uje(?:\s*si[eę])?)?\s*(?:at\s+|on\s+|o\s+|:\s*)?"
    rf"(?:(?P<wd>{WEEKDAY_WORD}),?\s+(?:at\s+|o\s+)?)?"
    rf"(?:(?P<mon>{MONTH_WORD})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?:at\s+)?)?"
    + CLOCK +
    rf"(?:\s+on\s+(?P<wd2>{WEEKDAY_WORD}))?",
    re.IGNORECASE)

# "wait until 9:30 AM when your plan usage resets" — the time comes first
RE_RESET_UNTIL = re.compile(
    rf"until\s+(?:(?P<wd>{WEEKDAY_WORD}),?\s+(?:at\s+)?)?" + CLOCK + r"(?=[^.]{0,80}?\breset)",
    re.IGNORECASE)

# relative form, e.g. "Resets in 2 hr 15 min" (English or Claude's Polish "za ... min")
RE_RESET_REL = re.compile(
    r"reset\w*\s+(?:in|za)\s+"
    r"(?:(\d+)\s*(?:hours?|hrs?|h|godz\w*)\.?)?\s*,?\s*"
    r"(?:(\d+)\s*(?:minut\w*|min(?:ute)?s?|m)\.?)?",
    re.IGNORECASE)

# short countdown, "Resets in 4:30" (minutes:seconds)
RE_RESET_COUNTDOWN = re.compile(r"reset\w*\s+(?:in|za)\s+(\d{1,2}):(\d{2})\b", re.IGNORECASE)

# Claude Code names the zone of its reset time: "resets 9:30am (Europe/Warsaw)"
RE_TIME_ZONE = re.compile(r"\((?P<zone>(?:[A-Za-z_]+/)+[A-Za-z0-9_+\-]+|UTC|GMT)\)")

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
          "sty": 1, "lut": 2, "kwi": 4, "maj": 5, "cze": 6,
          "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "paz": 10, "lis": 11, "gru": 12}

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "pon": 0, "wt": 1, "śr": 2, "sr": 2, "czw": 3, "pt": 4, "pią": 4, "pia": 4,
            "sob": 5, "ndz": 6, "nie": 6}

# A clock time that already passed today is the reset of a notice left on
# screen, unless it is so old that only tomorrow fits: a 5-hour window cannot
# end more than ~5 h after the limit is hit, so a same-day time more than 18 h
# in the past belongs to tomorrow ("resets 12:30 AM" read at 23:50).
STALE_RESET = dt.timedelta(hours=18)


def _weekday(word):
    word = (word or "").lower().rstrip(".")
    return WEEKDAYS.get(word[:3], WEEKDAYS.get(word[:2]))


def _clock(match):
    """(hour, minute) from a CLOCK match, or None for bare numbers ("resets 5")."""
    mm, ampm = match.group("mm"), match.group("ampm")
    h, minute = int(match.group("hh")), int(mm) if mm is not None else 0
    if ampm:
        if ampm.lower() == "pm" and h < 12:
            h += 12
        elif ampm.lower() == "am" and h == 12:
            h = 0
    return (h, minute) if 0 <= h <= 23 and 0 <= minute <= 59 else None


def _on_day(now, h, minute, weekday=None):
    t = now.replace(hour=h, minute=minute, second=0, microsecond=0)
    if weekday is not None:
        days = (weekday - now.weekday()) % 7
        t += dt.timedelta(days=days)
        if days == 0 and t <= now and now - t >= STALE_RESET:
            t += dt.timedelta(days=7)
    elif t <= now and now - t >= STALE_RESET:
        t += dt.timedelta(days=1)
    return t


def _zone(text):
    m = RE_TIME_ZONE.search(text)
    if not m:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(m.group("zone"))
    except Exception:  # unknown zone or no tz database: treat as local time
        return None


def parse_reset_time(text, now=None):
    """Return the reset datetime (naive, local time) extracted from text, or None."""
    now = now or dt.datetime.now()
    zone = _zone(text)
    # Clock times are resolved on the named zone's calendar, then made local.
    base = now.astimezone(zone).replace(tzinfo=None) if zone else now

    def local(t):
        return t.replace(tzinfo=zone).astimezone().replace(tzinfo=None) if zone else t

    m = RE_RESET_ABS.search(text)
    # reject matches without minutes/AM-PM/date (e.g. a stray "resets 5")
    if m and (m.group("mm") is not None or m.group("ampm") or m.group("mon")):
        clock = _clock(m)
        if clock and m.group("mon"):
            try:
                t = dt.datetime(base.year, MONTHS[m.group("mon").lower()[:3]], int(m.group("day")), *clock)
            except (KeyError, ValueError):
                t = None
            if t:
                if t < base - dt.timedelta(hours=12):
                    t = t.replace(year=base.year + 1)
                return local(t)
        elif clock:
            return local(_on_day(base, *clock, weekday=_weekday(m.group("wd") or m.group("wd2"))))

    m = RE_RESET_UNTIL.search(text)
    if m and (m.group("mm") is not None or m.group("ampm")):
        clock = _clock(m)
        if clock:
            return local(_on_day(base, *clock, weekday=_weekday(m.group("wd"))))

    m = RE_RESET_COUNTDOWN.search(text)
    if m:
        return now + dt.timedelta(minutes=int(m.group(1)), seconds=int(m.group(2)))

    m = RE_RESET_REL.search(text)
    if m and (m.group(1) or m.group(2)):
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        if hours or minutes:
            return now + dt.timedelta(hours=hours, minutes=minutes)
    return None


def detect_limit_banner(texts, now=None):
    """Scan control names (already filtered to outside-the-chat UI) for the
    "Usage limit reached" notice. The reset time ("Resets at 2:40 PM") sits in
    the same element or one of the next few, so parse a small window after the
    match. Returns (matched_text, reset_datetime_or_None); (None, None) when
    no banner is visible."""
    for i, tx in enumerate(texts):
        for pat in BANNER_LIMIT_PATTERNS:
            if re.search(pat, tx, re.IGNORECASE):
                window = "  ".join(texts[i:i + 4])
                return tx.strip(), parse_reset_time(window, now=now)
    return None, None

# -------------------------------------------------------------- Windows layer

MANUAL_IGNORE_S = 120        # after a command the user clicked, don't wait for calm

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake(armed):
    """Keep the system awake; when armed, keep the display on too."""
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if armed:
        flags |= ES_DISPLAY_REQUIRED
    kernel32.SetThreadExecutionState(flags)


def allow_sleep():
    kernel32.SetThreadExecutionState(ES_CONTINUOUS)


WS_EX_TRANSPARENT = 0x20


def click_through(hwnd):
    """True for a window the mouse passes through: an overlay an app draws over the screen,
    e.g. Claude's while it drives the computer. Never a window to read or click."""
    return bool(user32.GetWindowLongW(hwnd, -20) & WS_EX_TRANSPARENT)      # GWL_EXSTYLE


def process_exe_name(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)

# ---------------------------------------------------- one copy per watched window
# Two copies watching the same window would both type into the same conversation. A copy
# claims the window it watches with a named mutex. Copies watching different windows (a
# second Claude window, or Claude's and ChatGPT's) run side by side and take turns at the
# keyboard through input_lock.py.

ERROR_ALREADY_EXISTS = 183
_mutexes = ctypes.WinDLL("kernel32", use_last_error=True)
_mutexes.CreateMutexW.restype = ctypes.wintypes.HANDLE
_mutexes.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_mutexes.OpenMutexW.restype = ctypes.wintypes.HANDLE
_mutexes.OpenMutexW.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.LPCWSTR]
_mutexes.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]


def _claim_name(hwnd):
    return "Local\\AutoResume.Watching.%d" % hwnd


def claim_window(hwnd):
    """The claim on a window (a handle to close when done); None when another copy has it.
    Should Windows refuse the mutex altogether, the copy watches unguarded (True)."""
    ctypes.set_last_error(0)
    handle = _mutexes.CreateMutexW(None, False, _claim_name(hwnd))
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _mutexes.CloseHandle(handle)
        return None
    return handle


def release_claim(claim):
    if claim and claim is not True:
        _mutexes.CloseHandle(claim)


def claimed(hwnd):
    """Does some copy (this one included) watch the window?"""
    handle = _mutexes.OpenMutexW(0x00100000, False, _claim_name(hwnd))     # SYNCHRONIZE
    if handle:
        _mutexes.CloseHandle(handle)
    return bool(handle)

# --------------------------------------------------------------- monitor thread


class MonitorWorker(threading.Thread):
    """All UI Automation calls happen in this thread (its own COM apartment)."""

    IDLE, STARTING, MONITORING, ARMED, VERIFY = "IDLE", "STARTING", "MONITORING", "ARMED", "VERIFY"
    strings = STRINGS               # this product's UI and log texts
    engine_class = SessionEngine    # this product's accessibility adapter + scheduler

    def __init__(self, out_queue, cfg):
        super().__init__(daemon=True)
        self.out = out_queue          # (kind, data) -> UI
        self.cmds = queue.Queue()     # commands from UI
        self.cfg = dict(cfg)
        self.state = self.IDLE
        self.hwnd = None
        self.reset_at = None
        self.send_at = None
        self.start_deadline = None    # monotonic end of the start countdown
        self.start_until = None       # the same moment as a datetime, for the UI
        self.pending_arm = None       # manual reset time applied when the countdown ends
        self.guard = InputGuard(self)  # is anyone else using the mouse right now?
        self.pause_reason = None      # None, "user", "claude_cu" or "chatgpt_cu"
        self.claim = None             # this copy's claim on the window it watches
        self.claimed_hwnd = None
        self._stop_event = threading.Event()
        self.engine = self.engine_class(self)

    # --- API for the UI thread (thread-safe) ---
    def command(self, name, payload=None):
        self.cmds.put((name, payload))

    def shutdown(self):
        self._stop_event.set()

    # --- messages to the UI ---
    def emit(self, kind, data=None):
        self.out.put((kind, data))

    def t(self, key, **kw):
        return tr(self.cfg.get("language", "en"), key, table=self.strings, **kw)

    def text(self, key, **kw):
        """t() that remembers its key: put into a log line, it changes language with it."""
        return Text(self.t(key, **kw), key, kw)

    def log_path(self):
        return LOG_PATH

    def log(self, key, level="info", **kw):
        """Write a line to the log file (in the current language) and to the window, which
        keeps its key and shows it again in the other language after a switch."""
        stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        msg = self.text(key, **kw)
        line = Text(self.t("log_line", stamp=stamp, msg=msg), "log_line", dict(stamp=stamp, msg=msg))
        self.emit("log", (line, level))
        try:
            with open(self.log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- main loop
    def run(self):
        with auto.UIAutomationInitializerInThread():
            auto.SetGlobalSearchTimeout(3)
            while not self._stop_event.is_set():
                try:
                    self._process_commands()
                    self._tick()
                except Exception as e:  # the monitor must not die silently
                    self.log("log_monitor_error", "bad", err=repr(e))
                    time.sleep(2)
                time.sleep(0.5)
        self.release_window()
        allow_sleep()

    # ------------------------------------------------ the window this copy watches
    def _pick_window(self, wins):
        """The first window no other copy watches; with none free, the first one (shown,
        but starting to watch it is refused)."""
        free = [hwnd for hwnd, _ in wins if hwnd == self.claimed_hwnd or not claimed(hwnd)]
        return free[0] if free else (wins[0][0] if wins else None)

    def _claim_window(self):
        """Claim the watched window for this copy; False when another copy watches it."""
        if self.claim and self.claimed_hwnd == self.hwnd:
            return True
        self.release_window()
        claim = claim_window(self.hwnd)
        if not claim:
            return False
        self.claim, self.claimed_hwnd = claim, self.hwnd
        return True

    def release_window(self):
        release_claim(self.claim)
        self.claim = self.claimed_hwnd = None

    def _stop_watching(self):
        self.state = self.IDLE
        self.start_deadline = self.start_until = self.pending_arm = None
        self.engine.reset()
        self.reset_at = self.send_at = None
        self.release_window()
        allow_sleep()

    def _process_commands(self):
        while True:
            try:
                name, payload = self.cmds.get_nowait()
            except queue.Empty:
                return
            if name == "refresh_windows":
                wins = self._enum_windows()
                if self.hwnd not in {hwnd for hwnd, _ in wins}:
                    self.hwnd = self._pick_window(wins)
                    self.engine.reset()
                    self.engine.catalog.clear()
                self.emit("windows", wins)
                if wins:
                    self._refresh()
                else:
                    self.emit("chats", [])
                    self.emit("status", "no_window")
            elif name == "refresh_chats":
                self._refresh()
            elif name == "select_window":
                if self.hwnd != payload:
                    self.engine.reset()
                    self.engine.catalog.clear()
                    self.hwnd = payload
                    if self.state != self.IDLE and not self._claim_window():
                        self._stop_watching()     # never watch a window another copy watches
                        self.log("log_window_taken", "warn")
                        self.emit("state", self._state_info())
                    self._refresh()
            elif name == "start":
                self._begin()
            elif name == "stop":
                self._stop_watching()
                self.log("log_stopped")
                self.emit("state", self._state_info())
                self.engine.publish()     # don't leave cancelled timers in the list
            elif name == "send_now":
                if self.hwnd and self.claimed_hwnd != self.hwnd and claimed(self.hwnd):
                    self.log("log_window_taken", "warn")    # the copy watching it resumes it
                    continue
                self.log("log_manual_send")
                # The user just clicked our own window: don't wait for the mouse to
                # go quiet (the computer-use signals still apply).
                self.guard.ignore_user_for(MANUAL_IGNORE_S)
                panes = self._refresh()
                for key in self.engine.targets(panes or []):
                    if not self.cmds.empty() or self._stop_event.is_set():
                        break
                    try:
                        with input_lock(worker_cancelled(self)):
                            result = self.engine.ui.resume(key, self.cfg.get("prefer_try_again"))
                        from session_automation import SessionState
                        self.engine.sessions[key] = SessionState(
                            phase="watching" if result in ("busy", "cleared") else "verifying",
                            reason="limit", due=dt.datetime.now() + dt.timedelta(seconds=self.cfg["verify_delay_s"]))
                        self.log("log_chat_action", chat=key.split(":", 1)[-1], action=self.text(result))
                    except Exception as exc:
                        self.log("log_chat_action", "warn", chat=key.split(":", 1)[-1], action=self.text(str(exc)))
            elif name == "arm_manual":
                self.guard.ignore_user_for(MANUAL_IGNORE_S)
                if self.state == self.IDLE:
                    self._begin(arm=payload)
                elif self.state == self.STARTING:
                    self.pending_arm = payload
                else:
                    self.engine.arm(payload)
            elif name == "config":
                self.cfg.update(payload)
                self.engine.next_scan = 0

    def _refresh(self):
        """Re-read the window's conversations; None when it can't be read now."""
        try:
            return self.engine.refresh()
        except WindowHidden:
            self.emit("status", "hidden")
            self.log("log_hidden", "warn")
        except Exception as exc:
            self.log("log_monitor_error", "warn", err=self.text(str(exc)))
        return None

    def _begin(self, arm=None):
        """Start watching after a visible countdown, so the user can let go of
        the mouse and keyboard before the app starts switching chats."""
        if not self.hwnd:
            wins = self._enum_windows()
            if wins:
                self.hwnd = self._pick_window(wins)
                self.emit("windows", wins)
        if not self.hwnd:
            self.log("log_no_window_start", "warn")
            return
        if not self._claim_window():
            self.log("log_window_taken", "warn")
            return
        self.engine.reset()
        self.pending_arm = arm
        if self.cfg["keep_awake"]:
            keep_awake(False)
        delay = self.cfg.get("start_delay_s", 0)
        if delay <= 0:
            self._activate()
            return
        self.state = self.STARTING
        self.start_deadline = time.monotonic() + delay
        self.start_until = dt.datetime.now() + dt.timedelta(seconds=delay)
        self.log("log_starting", "warn", sec=delay)
        self.emit("state", self._state_info())
        self.engine.publish()

    def _activate(self):
        self.state = self.MONITORING
        self.start_deadline = self.start_until = None
        self.log("log_started")
        self.emit("state", self._state_info())
        if self.pending_arm is not None:
            arm, self.pending_arm = self.pending_arm, None
            self.engine.arm(arm)

    def _tick(self):
        if self.state == self.IDLE:
            return
        if self.cfg["keep_awake"]:
            keep_awake(True)
        else:
            allow_sleep()
        if self.state == self.STARTING:
            if time.monotonic() >= self.start_deadline:
                self._activate()
            return        # nothing touches Claude before the countdown ends
        self.engine.tick()

    # ---------------------------------------------------------------- scanning
    def _get_window(self):
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            if self.hwnd:
                # Don't migrate armed conversations to a different window.
                return None
            wins = self._enum_windows()
            if wins:
                self.hwnd = self._pick_window(wins)
                self.emit("windows", wins)
                self.log("log_window_refound", hwnd=self.hwnd)
            else:
                return None
        try:
            return auto.ControlFromHandle(self.hwnd)
        except Exception:
            return None

    def _enum_windows(self):
        """List of (hwnd, title) for claude.exe windows."""
        result = []
        try:
            for w in auto.GetRootControl().GetChildren():
                try:
                    if w.ClassName != "Chrome_WidgetWin_1" or click_through(w.NativeWindowHandle):
                        continue
                    name = w.Name or ""
                    exe = process_exe_name(w.ProcessId)
                    if exe == "claude.exe" or (not exe and "claude" in name.lower()):
                        result.append((w.NativeWindowHandle, name or "Claude"))
                except Exception:
                    continue
        except Exception:
            pass
        return result

    @staticmethod
    def _wake_accessibility(win):
        """Chromium builds the accessibility tree only when a client asks for it."""
        try:
            for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=80):
                if ctrl.ControlTypeName == "DocumentControl":
                    try:
                        tp = ctrl.GetTextPattern()
                        if tp:
                            tp.DocumentRange.GetText(32)
                    except Exception:
                        pass
                    try:
                        ctrl.GetChildren()
                    except Exception:
                        pass
        except Exception:
            pass

    # -------------------------------------------------- usage panel (5h limit)
    # The meter is a tiny round icon in the bottom bar; its Name is
    # "Usage: context X%, plan Y%". Two OTHER controls also start with "Usage"
    # and must never be clicked, or Claude Desktop navigates away and the app
    # ends up typing "continue" into the wrong chat (the v0.21 session-switch
    # bug): the "Usage limit reached" notice (a WIDE ButtonControl, often
    # scrolled off-screen) and a session titled "Usage…" (a TextControl).
    # We pick the meter by shape+place: a small ButtonControl in the bottom bar.
    USAGE_METER_MAX_W = 80        # px — the meter icon; the notice is ~170 wide
    USAGE_METER_BAND_FRAC = 0.15  # meter sits within this fraction of the bottom

    @staticmethod
    def _is_usage_meter(name, ctrl_type, rect, win_rect):
        """True only for the bottom-bar usage meter (see note above)."""
        if ctrl_type != "ButtonControl":
            return False
        if not (name or "").strip().lower().startswith("usage"):
            return False
        if win_rect is None or rect is None:
            return False              # no geometry -> refuse to click blindly
        win_h = win_rect.bottom - win_rect.top
        if win_h <= 0:
            return False
        if (rect.right - rect.left) > MonitorWorker.USAGE_METER_MAX_W:
            return False              # wide -> the "Usage limit reached" notice
        band = max(120, int(MonitorWorker.USAGE_METER_BAND_FRAC * win_h))
        center_y = (rect.top + rect.bottom) / 2
        return center_y >= win_rect.bottom - band

    @staticmethod
    def _find_usage_button(win):
        """The small round meter in the bottom bar; Name is 'Usage: …%'."""
        try:
            win_rect = win.BoundingRectangle
        except Exception:
            win_rect = None
        for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=150):
            try:
                name = ctrl.Name or ""
                if not name.strip().lower().startswith("usage"):
                    continue
                ct = ctrl.ControlTypeName
                rect = ctrl.BoundingRectangle
            except Exception:
                continue
            if MonitorWorker._is_usage_meter(name, ct, rect, win_rect):
                return ctrl
        return None

    @staticmethod
    def _find_usage_panel(win):
        """The 'Usage' popover that appears after clicking the meter."""
        for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=150):
            try:
                if (ctrl.Name or "").strip() == "Usage" and \
                        ctrl.ControlTypeName in ("WindowControl", "GroupControl"):
                    return ctrl
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_usage_rows(panel):
        """Parse the flat 'label / Resets in X / N% / bar' list into per-limit
        dicts. Returns {'5h': {pct, reset}, 'weekly': {...}, 'weekly_fable': {...}}."""
        texts = []
        try:
            for ctrl, _ in auto.WalkControl(panel, includeTop=True, maxDepth=40):
                try:
                    n = (ctrl.Name or "").strip()
                except Exception:
                    continue
                if n:
                    texts.append(n)
        except Exception:
            return {}
        rows, cur = {}, None
        for tx in texts:
            low = tx.lower()
            if "5-hour" in low:
                cur = "5h"; rows.setdefault(cur, {})
            elif low.startswith("weekly") and "fable" in low:
                cur = "weekly_fable"; rows.setdefault(cur, {})
            elif low.startswith("weekly"):
                cur = "weekly"; rows.setdefault(cur, {})
            elif low.startswith(("context", "plan usage", "view usage", "usage")):
                cur = None
            elif cur:
                mp = re.fullmatch(r"(\d{1,3})\s*%", tx)
                if mp and "pct" not in rows[cur]:
                    rows[cur]["pct"] = int(mp.group(1))
                elif "reset" in low and "reset" not in rows[cur]:
                    rt = parse_reset_time(tx)
                    if rt:
                        rows[cur]["reset"] = rt
        return rows

    def _read_usage_panel(self, win, meter_scope=None):
        """Open the usage popover, read the per-limit rows, close it and restore
        the mouse cursor. Returns (rows, ok)."""
        btn = self._find_usage_button(meter_scope if meter_scope is not None else win)
        if not btn:
            return {}, False
        if self.guard.user_busy():
            return {}, False      # someone else has the mouse; the panel can wait
        self._focus_window(self.hwnd)
        if user32.GetForegroundWindow() != self.hwnd or not self.cmds.empty() or self._stop_event.is_set():
            return {}, False
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        m0 = (pt.x, pt.y)
        try:
            if btn.IsOffscreen or not btn.IsEnabled:
                return {}, False
            with self.guard.sending():
                btn.Click(simulateMove=False)
        except Exception:
            return {}, False
        time.sleep(1.0)
        rows, ok = {}, False
        try:
            self._wake_accessibility(win)
            panel = self._find_usage_panel(win)
            if panel:
                rows = self._parse_usage_rows(panel)
                ok = "5h" in rows
        except Exception:
            ok = False
        try:
            if user32.GetForegroundWindow() == self.hwnd:
                with self.guard.sending():
                    auto.SendKeys("{Esc}", waitTime=0.05)
        except Exception:
            pass
        time.sleep(0.35)
        try:
            with self.guard.sending():
                user32.SetCursorPos(m0[0], m0[1])
        except Exception:
            pass
        return rows, ok

    # ------------------------------------------------------------------ sending
    @staticmethod
    def _escape_sendkeys(s):
        """uiautomation treats { and } as key-sequence delimiters; escape them
        so an arbitrary user message is typed literally."""
        out = []
        for c in s:
            if c == "{":
                out.append("{{}")
            elif c == "}":
                out.append("{}}")
            else:
                out.append(c)
        return "".join(out)

    def _focus_window(self, hwnd):
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.6)
        # A monitor thread has no foreground activation rights after the user
        # interacts with Auto-Resume. Temporarily join the foreground input
        # queue; always detach, even if activation fails. No synthetic Alt key
        # (which could operate a menu in an unrelated application).
        current = kernel32.GetCurrentThreadId()
        foreground = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        target = user32.GetWindowThreadProcessId(hwnd, None)
        attached = []
        try:
            for thread in {foreground, target} - {0, current}:
                if user32.AttachThreadInput(current, thread, True):
                    attached.append(thread)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            for thread in attached:
                user32.AttachThreadInput(current, thread, False)
        time.sleep(0.25)

    def _state_info(self):
        return {
            "state": self.state,
            "reset_at": self.reset_at,
            "send_at": self.send_at,
            "start_until": self.start_until,
        }

# ------------------------------------------------------------------ UI theme

THEME = {
    "bg":        "#14161B",   # night — warm graphite
    "panel":     "#1C1F26",
    "panel_hi":  "#242935",
    "border":    "#2A2E38",
    "text":      "#E9E4D6",   # parchment under a dimmed lamp
    "muted":     "#8B92A0",
    "log_bg":    "#101216",
    "log_fg":    "#A8B0BE",
    "green":     "#7BAE7F",   # watching
    "amber":     "#E0A458",   # limit / armed
    "amber_dim": "#8A6A3F",
    "blue":      "#7FA8D9",   # verifying
    "red":       "#D98080",
    "track":     "#2A2E38",
    "hover":     "#2E3543",   # a control under the pointer
    "press":     "#38404F",   # ...and while it is held down
}

# state -> lamp color key + label string key
STATE_COLOR = {"IDLE": "muted", "STARTING": "amber", "MONITORING": "green",
               "ARMED": "amber", "VERIFY": "blue", "PAUSED": "amber"}
STATE_LABEL = {"IDLE": "state_idle", "STARTING": "state_starting", "MONITORING": "state_monitoring",
               "ARMED": "state_armed", "VERIFY": "state_verify", "PAUSED": "state_paused"}


def pick_fonts(root):
    import tkinter.font as tkfont
    fams = set(tkfont.families(root))
    mono = "Cascadia Mono" if "Cascadia Mono" in fams else "Consolas"
    ui = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI"
    return ui, mono


def enable_dark_titlebar(root):
    try:
        root.update_idletasks()
        hwnd = user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (and older variant)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


class App(tk.Tk):
    # What differs per watched app; ChatGPT Auto-Resume subclasses this window.
    TITLE = "Claude Auto-Resume"
    WORDMARK = "CLAUDE AUTO-RESUME"
    WORKER = MonitorWorker
    FEATURES = ("prefer_try_again", "retry_api_errors", "auto_approach", "auto_permissions",
                "pause_on_foreign_input", "handover_enabled")
    LOG_KEEP = 2000              # log lines the window keeps (and shows again after a language switch)

    def __init__(self):
        super().__init__()
        self.title(self.TITLE)
        self.geometry("860x960")
        self.minsize(800, 660)
        self.configure(bg=THEME["bg"])

        self.cfg = self._load_config()
        self.lang = self.cfg.get("language", "en")
        self.out_queue = queue.Queue()
        self.worker = self.WORKER(self.out_queue, self.cfg)
        self.windows = []            # [(hwnd, title)]
        self.chat_rows = []          # worker order: Claude's newest-first sidebar order
        self.view_rows = []          # rows as displayed (sorted); tree iids index into this
        self.sort_column = None      # None = Claude's own order
        self.sort_descending = False
        self.help_tip = None         # tooltip window for the "?" next to an option
        self.help_label = None
        self.help_key = None
        self.help_icons = {}         # key -> its "?" icon, which the tooltip is placed beside
        self._help_timer = None
        self.checked_chats = set(self.cfg.get("selected_chats", []))
        self.log_lines = collections.deque(maxlen=self.LOG_KEEP)   # (Text line, level)
        self.state_info = {"state": "IDLE", "reset_at": None, "send_at": None}
        self.usage = {}              # used / plan / reset / session
        self.armed_since = None
        self.last_status = ""        # "" | "no_window" | "ok"

        self.font_ui, self.font_mono = pick_fonts(self)
        self._build_styles()
        self._build_ui()
        self._retext()
        self._update_lang_buttons()
        enable_dark_titlebar(self)

        self.worker.start()
        self.worker.command("refresh_windows")
        self.after(200, self._poll_queue)
        self.after(250, self._tick_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------- style
    def _build_styles(self):
        t = THEME
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=t["bg"], foreground=t["text"],
                        bordercolor=t["border"], focuscolor=t["amber"],
                        lightcolor=t["border"], darkcolor=t["border"],
                        troughcolor=t["bg"], font=(self.font_ui, 10))
        # clam paints anything hovered or held that has no rule of its own light grey
        # (#eeebe7): a hovered heading of the conversation list went white and its text
        # disappeared. Hover and press stay dark here, one step lighter each.
        style.map(".", background=[("disabled", t["panel"]), ("pressed", t["press"]),
                                   ("active", t["hover"])],
                  foreground=[("disabled", t["muted"])])
        style.configure("TFrame", background=t["bg"])
        style.configure("Panel.TFrame", background=t["panel"])
        style.configure("TLabel", background=t["bg"], foreground=t["text"])
        style.configure("Panel.TLabel", background=t["panel"])
        style.configure("Section.TLabel", background=t["bg"], foreground=t["muted"],
                        font=(self.font_ui, 9, "bold"))
        style.configure("TButton", background=t["panel_hi"], foreground=t["text"],
                        borderwidth=1, padding=(14, 7))
        style.map("TButton",
                  background=[("disabled", t["panel"]), ("pressed", t["press"]), ("active", t["hover"])],
                  foreground=[("disabled", t["muted"])],
                  lightcolor=[("pressed", t["press"])], darkcolor=[("pressed", t["press"])])
        style.configure("Primary.TButton", background="#2E4433",
                        foreground="#D6EBD8")
        style.map("Primary.TButton",
                  background=[("disabled", t["panel"]), ("pressed", "#2A3D2F"), ("active", "#38523E")],
                  foreground=[("disabled", t["muted"])])
        style.configure("Panel.TCheckbutton", background=t["panel"],
                        foreground=t["text"], indicatorbackground=t["panel_hi"],
                        indicatorforeground=t["amber"])
        style.map("Panel.TCheckbutton",
                  background=[("active", t["panel"])],
                  indicatorbackground=[("selected", t["panel_hi"])],
                  indicatorforeground=[("selected", t["amber"])])
        style.configure("TCombobox", fieldbackground=t["panel_hi"],
                        background=t["panel_hi"], foreground=t["text"],
                        arrowcolor=t["text"], selectbackground=t["panel_hi"],
                        selectforeground=t["text"])
        style.map("TCombobox", fieldbackground=[("readonly", t["panel_hi"])],
                  background=[("pressed", t["press"]), ("active", t["hover"])])
        style.configure("TEntry", fieldbackground=t["panel_hi"],
                        foreground=t["text"], insertcolor=t["text"])
        style.map("TEntry", background=[("readonly", t["panel"])],
                  lightcolor=[("focus", t["amber"])], darkcolor=[("focus", t["amber"])])
        style.configure("TSpinbox", fieldbackground=t["panel_hi"],
                        background=t["panel_hi"], foreground=t["text"],
                        arrowcolor=t["text"], insertcolor=t["text"])
        style.map("TSpinbox", background=[("readonly", t["panel_hi"]), ("pressed", t["press"]),
                                          ("active", t["hover"])])
        style.configure("Treeview", background=t["log_bg"], fieldbackground=t["log_bg"],
                        foreground=t["text"], rowheight=28, borderwidth=0)
        style.map("Treeview", background=[("selected", t["panel_hi"])],
                  foreground=[("selected", t["text"])])
        style.configure("Treeview.Heading", background=t["panel_hi"], foreground=t["text"], padding=6)
        # The headings sort the list: under the pointer they light up in the accent colour.
        style.map("Treeview.Heading", background=[("pressed", t["press"]), ("active", t["hover"])],
                  foreground=[("pressed", t["text"]), ("active", t["amber"])])
        style.configure("TNotebook", background=t["panel"], borderwidth=0)
        style.configure("TNotebook.Tab", background=t["panel_hi"], foreground=t["text"], padding=(14, 8))
        style.map("TNotebook.Tab", background=[("selected", t["panel"]), ("active", t["hover"])],
                  foreground=[("selected", t["amber"])],
                  lightcolor=[("selected", t["panel"]), ("!selected", t["border"])])
        style.configure("Vertical.TScrollbar", background=t["panel_hi"],
                        troughcolor=t["log_bg"], arrowcolor=t["muted"],
                        bordercolor=t["border"], lightcolor=t["panel_hi"],
                        darkcolor=t["panel_hi"], gripcount=0)
        style.map("Vertical.TScrollbar",
                  background=[("pressed", t["press"]), ("active", t["hover"])],
                  arrowcolor=[("active", t["text"])])
        self.option_add("*TCombobox*Listbox.background", t["panel_hi"])
        self.option_add("*TCombobox*Listbox.foreground", t["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["amber_dim"])
        self.option_add("*TCombobox*Listbox.selectForeground", t["text"])

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        t = THEME
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True)
        self.page_canvas = tk.Canvas(outer, bg=t["bg"], highlightthickness=0)
        page_scroll = ttk.Scrollbar(outer, orient="vertical", command=self.page_canvas.yview)
        page_scroll.pack(side="right", fill="y")
        self.page_canvas.pack(side="left", fill="both", expand=True)
        self.page_canvas.configure(yscrollcommand=page_scroll.set)
        root = ttk.Frame(self.page_canvas, padding=12)
        page_id = self.page_canvas.create_window(0, 0, anchor="nw", window=root)
        root.bind("<Configure>", lambda _e: self.page_canvas.configure(scrollregion=self.page_canvas.bbox("all")))
        self.page_canvas.bind("<Configure>", lambda e: self.page_canvas.itemconfigure(page_id, width=e.width))

        # --- top bar: wordmark + EN/PL toggle ---
        top = tk.Frame(root, bg=t["bg"])
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text=self.WORDMARK, bg=t["bg"], fg=t["muted"],
                 font=(self.font_ui, 9, "bold")).pack(side="left")
        seg = tk.Frame(top, bg=t["border"])
        seg.pack(side="right")
        self.lang_btns = {}
        for code in ("en", "pl"):
            b = tk.Label(seg, text=code.upper(), bg=t["panel_hi"], fg=t["muted"],
                         font=(self.font_ui, 8, "bold"), width=3, pady=2,
                         cursor="hand2")
            b.pack(side="left", padx=1, pady=1)
            b.bind("<Button-1>", lambda _e, c=code: self._set_lang(c))
            self.lang_btns[code] = b

        # --- instrument panel: lamp, state, big clock, progress, usage ---
        head = tk.Frame(root, bg=t["panel"], highlightbackground=t["border"],
                        highlightthickness=1)
        head.pack(fill="x")
        head_in = tk.Frame(head, bg=t["panel"])
        head_in.pack(fill="x", padx=14, pady=(12, 10))

        row_state = tk.Frame(head_in, bg=t["panel"])
        row_state.pack(fill="x")
        self.lamp = tk.Canvas(row_state, width=14, height=14, bg=t["panel"],
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=4)
        self.lamp_id = self.lamp.create_oval(2, 2, 12, 12,
                                             fill=t["muted"], outline="")
        self.lbl_state = tk.Label(row_state, text="", bg=t["panel"], fg=t["text"],
                                  font=(self.font_ui, 13, "bold"))
        self.lbl_state.pack(side="left", padx=(8, 0))
        self.lbl_clock = tk.Label(row_state, text="--:--:--", bg=t["panel"],
                                  fg=t["muted"], font=(self.font_mono, 26, "bold"))
        self.lbl_clock.pack(side="right")

        self.lbl_caption = tk.Label(head_in, text="", bg=t["panel"], fg=t["muted"],
                                    font=(self.font_ui, 9), anchor="w")
        self.lbl_caption.pack(fill="x", pady=(2, 8))

        self.progress = tk.Canvas(head_in, height=4, bg=t["track"],
                                  highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress_fill = self.progress.create_rectangle(
            0, 0, 0, 4, fill=t["amber"], outline="")

        row_usage = tk.Frame(head_in, bg=t["panel"])
        row_usage.pack(fill="x", pady=(10, 0))
        self.lbl_used = tk.Label(row_usage, text="", bg=t["panel"],
                                 fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_used.pack(side="left")
        self.bar_used = tk.Canvas(row_usage, width=90, height=5, bg=t["track"],
                                  highlightthickness=0)
        self.bar_used.pack(side="left", padx=(6, 18), pady=1)
        self.bar_used_fill = self.bar_used.create_rectangle(
            0, 0, 0, 5, fill=t["green"], outline="")
        self.lbl_plan = tk.Label(row_usage, text="", bg=t["panel"],
                                 fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_plan.pack(side="left")
        self.bar_plan = tk.Canvas(row_usage, width=90, height=5, bg=t["track"],
                                  highlightthickness=0)
        self.bar_plan.pack(side="left", padx=(6, 18), pady=1)
        self.bar_plan_fill = self.bar_plan.create_rectangle(
            0, 0, 0, 5, fill=t["green"], outline="")
        self.lbl_reset_seen = tk.Label(row_usage, text="", bg=t["panel"],
                                       fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_reset_seen.pack(side="right")

        # ----------------------------------------------------------- control
        self.sec_control = ttk.Label(root, text="", style="Section.TLabel")
        self.sec_control.pack(anchor="w", pady=(14, 4))
        panel = tk.Frame(root, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1)
        panel.pack(fill="x")
        panel_in = tk.Frame(panel, bg=t["panel"])
        panel_in.pack(fill="x", padx=14, pady=10)

        row_win = tk.Frame(panel_in, bg=t["panel"])
        row_win.pack(fill="x", pady=(0, 8))
        self.lbl_window = tk.Label(row_win, text="", bg=t["panel"], fg=t["text"],
                                   font=(self.font_ui, 10))
        self.lbl_window.pack(side="left")
        self.cmb_windows = ttk.Combobox(row_win, state="readonly", width=40)
        self.cmb_windows.pack(side="left", padx=8, fill="x", expand=True)
        self.cmb_windows.bind("<<ComboboxSelected>>", self._on_window_selected)
        self.btn_refresh = ttk.Button(
            row_win, text="", command=lambda: self.worker.command("refresh_windows"))
        self.btn_refresh.pack(side="left")

        row_btn = tk.Frame(panel_in, bg=t["panel"])
        row_btn.pack(fill="x", pady=(0, 8))
        self.btn_start = ttk.Button(row_btn, text="", style="Primary.TButton",
                                    command=self._on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row_btn, text="", command=self._on_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_send_now = ttk.Button(row_btn, text="", command=self._on_send_now)
        self.btn_send_now.pack(side="right")

        self.notebook = ttk.Notebook(panel_in)
        self.notebook.pack(fill="both", expand=True)
        self.chats_tab = tk.Frame(self.notebook, bg=t["panel"], padx=2, pady=10)
        self.settings_tab = tk.Frame(self.notebook, bg=t["panel"], padx=2, pady=10)
        self.notebook.add(self.chats_tab, text="Conversations")
        self.notebook.add(self.settings_tab, text="Settings")
        # What is watched follows from the list itself: nothing checked means the
        # conversations open in Claude, otherwise exactly the checked ones. This line says
        # which of the two is in force.
        scope_row = tk.Frame(self.chats_tab, bg=t["panel"])
        scope_row.pack(fill="x", pady=(0, 8))
        self.lbl_watching = tk.Label(scope_row, anchor="w", bg=t["panel"], fg=t["text"],
                                     font=(self.font_ui, 10, "bold"))
        self.lbl_watching.pack(side="left", fill="x", expand=True, padx=(2, 8))
        self.btn_chats_refresh = ttk.Button(scope_row, command=lambda: self.worker.command("refresh_chats"))
        self.btn_chats_refresh.pack(side="right")
        self.btn_default_order = ttk.Button(scope_row, command=self._default_order, state="disabled")
        self.btn_default_order.pack(side="right", padx=(0, 8))
        tree_wrap = tk.Frame(self.chats_tab, bg=t["panel"])
        tree_wrap.pack(fill="x")
        self.tree_chats = ttk.Treeview(tree_wrap, columns=("watch", "chat", "source", "status"),
                                       show="headings", height=5, selectmode="browse")
        for column, width in (("watch", 55), ("chat", 330), ("source", 110), ("status", 210)):
            self.tree_chats.column(column, width=width, minwidth=45, stretch=column in ("chat", "status"))
            self.tree_chats.heading(column, command=lambda c=column: self._sort_by(c))
        self.tree_chats.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree_chats.yview)
        scroll.pack(side="right", fill="y")
        self.tree_chats.configure(yscrollcommand=scroll.set)
        self.tree_chats.bind("<ButtonRelease-1>", self._toggle_chat)
        self.tree_chats.bind("<space>", self._toggle_chat)
        self.tree_chats.bind("<Return>", self._toggle_chat)
        self.lbl_chats_hint = tk.Label(self.chats_tab, anchor="w", justify="left", wraplength=745,
                                        bg=t["panel"], fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_chats_hint.pack(fill="x", pady=(6, 10))
        self.feature_vars = {}
        self.feature_checks = {}
        self.feature_help = {}
        for key in self.FEATURES:
            var = tk.BooleanVar(value=self.cfg.get(key, False))
            row = tk.Frame(self.chats_tab, bg=t["panel"])
            row.pack(anchor="w", pady=2)
            check = ttk.Checkbutton(row, variable=var, style="Panel.TCheckbutton", command=self._push_config)
            check.pack(side="left")
            icon = self._help_icon(row, key)
            icon.pack(side="left", padx=(6, 0))
            self.feature_vars[key], self.feature_checks[key], self.feature_help[key] = var, check, icon

        panel_in = self.settings_tab

        row_opt = tk.Frame(panel_in, bg=t["panel"])
        row_opt.pack(fill="x", pady=(0, 8))
        self.lbl_scan_every = tk.Label(row_opt, text="", bg=t["panel"],
                                       fg=t["text"], font=(self.font_ui, 10))
        self.lbl_scan_every.pack(side="left")
        self.var_interval = tk.IntVar(value=self.cfg["scan_interval_s"])
        ttk.Spinbox(row_opt, from_=5, to=300, width=4,
                    textvariable=self.var_interval,
                    command=self._push_config).pack(side="left", padx=4)
        self.lbl_seconds = tk.Label(row_opt, text="", bg=t["panel"], fg=t["text"],
                                    font=(self.font_ui, 10))
        self.lbl_seconds.pack(side="left", padx=(0, 16))
        self.var_autosend = tk.BooleanVar(value=self.cfg["auto_send"])
        self.chk_autosend = ttk.Checkbutton(
            row_opt, text="", style="Panel.TCheckbutton",
            variable=self.var_autosend, command=self._push_config)
        self.chk_autosend.pack(side="left", padx=(0, 16))
        self.var_awake = tk.BooleanVar(value=self.cfg["keep_awake"])
        self.chk_awake = ttk.Checkbutton(
            row_opt, text="", style="Panel.TCheckbutton",
            variable=self.var_awake, command=self._push_config)
        self.chk_awake.pack(side="left")

        row_start = tk.Frame(panel_in, bg=t["panel"])
        row_start.pack(fill="x", pady=(0, 8))
        self.lbl_start_delay = tk.Label(row_start, text="", bg=t["panel"],
                                        fg=t["text"], font=(self.font_ui, 10))
        self.lbl_start_delay.pack(side="left")
        self.var_start_delay = tk.IntVar(value=self.cfg["start_delay_s"])
        ttk.Spinbox(row_start, from_=0, to=120, width=4,
                    textvariable=self.var_start_delay,
                    command=self._push_config).pack(side="left", padx=4)
        self.lbl_start_delay_unit = tk.Label(row_start, text="", bg=t["panel"], fg=t["text"],
                                             font=(self.font_ui, 10))
        self.lbl_start_delay_unit.pack(side="left")

        row_quiet = tk.Frame(panel_in, bg=t["panel"])
        row_quiet.pack(fill="x", pady=(0, 8))
        self.lbl_quiet = tk.Label(row_quiet, text="", bg=t["panel"], fg=t["text"],
                                  font=(self.font_ui, 10))
        self.lbl_quiet.pack(side="left")
        self.var_quiet = tk.IntVar(value=self.cfg["foreign_input_quiet_s"])
        ttk.Spinbox(row_quiet, from_=5, to=300, width=4, textvariable=self.var_quiet,
                    command=self._push_config).pack(side="left", padx=4)
        self.lbl_quiet_unit = tk.Label(row_quiet, text="", bg=t["panel"], fg=t["text"],
                                       font=(self.font_ui, 10))
        self.lbl_quiet_unit.pack(side="left")

        # The handover's own settings, in a group of their own that is shown only while
        # "Hand the work over to a new conversation" is on (see _show_handover_rows).
        self.handover_rows = tk.Frame(panel_in, bg=t["panel"], highlightthickness=1,
                                      highlightbackground=t["border"], padx=10, pady=8)
        self.lbl_sec_handover = tk.Label(self.handover_rows, text="", anchor="w", bg=t["panel"],
                                         fg=t["muted"], font=(self.font_ui, 8, "bold"))
        self.lbl_sec_handover.pack(fill="x", pady=(0, 6))

        row_hand = tk.Frame(self.handover_rows, bg=t["panel"])
        row_hand.pack(fill="x", pady=(0, 8))
        self.lbl_threshold = tk.Label(row_hand, text="", bg=t["panel"], fg=t["text"],
                                      font=(self.font_ui, 10))
        self.lbl_threshold.pack(side="left")
        self.var_threshold = tk.StringVar(value=self.cfg["handover_threshold"])
        ent_threshold = ttk.Entry(row_hand, width=8, textvariable=self.var_threshold)
        ent_threshold.pack(side="left", padx=4)
        ent_threshold.bind("<FocusOut>", lambda _e: self._push_config())
        ent_threshold.bind("<Return>", lambda _e: self._push_config())
        self.lbl_threshold_hint = tk.Label(row_hand, text="", bg=t["panel"], fg=t["muted"],
                                           font=(self.font_ui, 9))
        self.lbl_threshold_hint.pack(side="left", padx=(6, 6))
        self._help_icon(row_hand, "handover_threshold").pack(side="left")

        row_plan = tk.Frame(self.handover_rows, bg=t["panel"])
        row_plan.pack(fill="x", pady=(0, 8))
        self.lbl_plan_folder = tk.Label(row_plan, text="", bg=t["panel"], fg=t["text"],
                                        font=(self.font_ui, 10))
        self.lbl_plan_folder.pack(side="left")
        self._help_icon(row_plan, "plan_folder").pack(side="left", padx=(6, 0))
        self.btn_plan_pick = ttk.Button(row_plan, text="", command=self._pick_plan_folder)
        self.btn_plan_pick.pack(side="right")
        self.var_plan = tk.StringVar(value=self.cfg["plan_folder"])
        ent_plan = ttk.Entry(row_plan, textvariable=self.var_plan)
        ent_plan.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ent_plan.bind("<FocusOut>", lambda _e: self._push_config())
        ent_plan.bind("<Return>", lambda _e: self._push_config())

        row_texts = tk.Frame(self.handover_rows, bg=t["panel"])
        row_texts.pack(fill="x")
        self.btn_handover_texts = ttk.Button(row_texts, text="", command=self._edit_handover_texts)
        self.btn_handover_texts.pack(side="left")
        self._help_icon(row_texts, "handover_texts").pack(side="left", padx=(8, 0))

        row_msg = tk.Frame(panel_in, bg=t["panel"])
        row_msg.pack(fill="x", pady=(0, 8))
        self.row_msg = row_msg
        row_msg_head = tk.Frame(row_msg, bg=t["panel"])
        row_msg_head.pack(fill="x")
        self.lbl_message = tk.Label(row_msg_head, text="", bg=t["panel"],
                                    fg=t["text"], font=(self.font_ui, 10))
        self.lbl_message.pack(side="left")
        self.lbl_msg_hint = tk.Label(row_msg_head, text="", bg=t["panel"],
                                     fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_msg_hint.pack(side="left", padx=8)

        msg_wrap = tk.Frame(row_msg, bg=t["border"])
        msg_wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.txt_message = tk.Text(
            msg_wrap, height=self._msg_lines(), wrap="word",
            font=(self.font_ui, 10), bg=t["log_bg"], fg=t["text"],
            insertbackground=t["text"], relief="flat", highlightthickness=0,
            selectbackground=t["amber_dim"], padx=8, pady=6)
        msg_scroll = ttk.Scrollbar(msg_wrap, orient="vertical",
                                   command=self.txt_message.yview)
        self.txt_message.configure(yscrollcommand=msg_scroll.set)
        self.txt_message.pack(side="left", fill="both", expand=True,
                              padx=(1, 0), pady=1)
        msg_scroll.pack(side="right", fill="y", pady=1, padx=(0, 1))
        self.txt_message.insert("1.0", self.cfg.get("message") or DEFAULT_MESSAGE)
        self.txt_message.bind("<FocusOut>", lambda _e: self._commit_message())

        self.grip_msg = tk.Canvas(row_msg, height=9, bg=t["panel"],
                                  highlightthickness=0,
                                  cursor="sb_v_double_arrow")
        self.grip_msg.pack(fill="x")
        self.grip_msg.bind("<Configure>", lambda _e: self._draw_grip())
        self.grip_msg.bind("<Button-1>", self._on_grip_press)
        self.grip_msg.bind("<B1-Motion>", self._on_grip_drag)
        self.grip_msg.bind("<ButtonRelease-1>", lambda _e: self._on_grip_release())

        row_manual = tk.Frame(panel_in, bg=t["panel"])
        row_manual.pack(fill="x")
        self.lbl_know_reset = tk.Label(row_manual, text="", bg=t["panel"],
                                       fg=t["text"], font=(self.font_ui, 10))
        self.lbl_know_reset.pack(side="left")
        self.ent_manual = ttk.Entry(row_manual, width=7)
        self.ent_manual.pack(side="left", padx=8)
        self.ent_manual.bind("<Return>", lambda _e: self._on_arm_manual())
        self.btn_arm = ttk.Button(row_manual, text="", command=self._on_arm_manual)
        self.btn_arm.pack(side="left")
        self.lbl_arm_hint = tk.Label(row_manual, text="", bg=t["panel"],
                                     fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_arm_hint.pack(side="left", padx=10)

        # -------------------------------------------------------------- log
        self.sec_log = ttk.Label(root, text="", style="Section.TLabel")
        self.sec_log.pack(anchor="w", pady=(14, 4))
        log_wrap = tk.Frame(root, bg=t["border"])
        log_wrap.pack(fill="both", expand=True)
        self.txt_log = tk.Text(
            log_wrap, height=10, state="disabled", font=(self.font_mono, 9),
            bg=t["log_bg"], fg=t["log_fg"], insertbackground=t["text"],
            relief="flat", highlightthickness=0, wrap="word",
            selectbackground=t["amber_dim"], padx=10, pady=8)
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical",
                                   command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_scroll.set)
        self.txt_log.pack(side="left", fill="both", expand=True,
                          padx=(1, 0), pady=1)
        log_scroll.pack(side="right", fill="y", pady=1, padx=(0, 1))
        self.txt_log.tag_configure("warn", foreground=t["amber"])
        self.txt_log.tag_configure("good", foreground=t["green"])
        self.txt_log.tag_configure("bad", foreground=t["red"])

        self.progress.bind("<Configure>", lambda _e: self._draw_progress())
        self._show_handover_rows()

    def _show_handover_rows(self):
        """The threshold, the plan folder and the messages mean something only while the
        handover is on, so they are shown only then (in their old place in Settings)."""
        if self.feature_vars["handover_enabled"].get():
            if not self.handover_rows.winfo_manager():
                self.handover_rows.pack(fill="x", pady=(0, 10), before=self.row_msg)
        else:
            self.handover_rows.pack_forget()

    # ---------------------------------------------------------------- settings
    def _load_config(self):
        return load_config()

    def _save_config(self):
        save_config(self.cfg)

    # ------------------------------------------------------------------ i18n
    def _T(self, key, **kw):
        return tr(self.lang, key, table=self.WORKER.strings, **kw)

    def _set_lang(self, code):
        if code == self.lang or code not in ("en", "pl"):
            return
        self.lang = code
        self.cfg["language"] = code
        self._save_config()
        self.worker.command("config", {"language": code})
        self._update_lang_buttons()
        self._retext()

    def _update_lang_buttons(self):
        for code, btn in self.lang_btns.items():
            active = code == self.lang
            btn.config(fg=THEME["amber"] if active else THEME["muted"],
                       bg=THEME["panel_hi"] if active else THEME["panel"])

    def _retext(self):
        """Re-apply all static strings in the current language."""
        self.sec_control.config(text=self._T("sec_control"))
        self.sec_log.config(text=self._T("sec_log"))
        self.lbl_window.config(text=self._T("lbl_window"))
        self.btn_refresh.config(text=self._T("btn_refresh"))
        self.btn_start.config(text=self._T("btn_start"))
        self.btn_stop.config(text=self._T("btn_stop"))
        self.btn_send_now.config(text=self._T("btn_send_now"))
        self.lbl_scan_every.config(text=self._T("lbl_scan_every"))
        self.lbl_seconds.config(text=self._T("lbl_seconds"))
        self.lbl_start_delay.config(text=self._T("lbl_start_delay"))
        self.lbl_start_delay_unit.config(text=self._T("lbl_seconds"))
        self.lbl_quiet.config(text=self._T("lbl_quiet"))
        self.lbl_quiet_unit.config(text=self._T("lbl_seconds"))
        self.lbl_threshold.config(text=self._T("lbl_threshold"))
        self.lbl_threshold_hint.config(text=self._T("hint_threshold"), fg=THEME["muted"])
        self.btn_handover_texts.config(text=self._T("btn_handover_texts"))
        self.lbl_sec_handover.config(text=self._T("sec_handover"))
        self.lbl_plan_folder.config(text=self._T("lbl_plan"))
        self.btn_plan_pick.config(text=self._T("btn_plan_pick"))
        self.chk_autosend.config(text=self._T("chk_autosend"))
        self.chk_awake.config(text=self._T("chk_awake"))
        self.lbl_message.config(text=self._T("lbl_message"))
        self.lbl_msg_hint.config(text=self._T("hint_message"))
        self.lbl_know_reset.config(text=self._T("lbl_know_reset"))
        self.btn_arm.config(text=self._T("btn_arm"))
        self.lbl_arm_hint.config(text=self._T("lbl_arm_hint"))
        self.notebook.tab(self.chats_tab, text="Rozmowy" if self.lang == "pl" else "Conversations")
        self.notebook.tab(self.settings_tab, text="Ustawienia" if self.lang == "pl" else "Settings")
        self.btn_chats_refresh.config(text=self._T("chats_refresh"))
        for key, check in self.feature_checks.items():
            check.config(text=self._T(key))
        if self.help_key:
            self._show_help(self.help_key)
        self.btn_default_order.config(text=self._T("btn_default_order"))
        # The source column as wide as its longest label in this language: ChatGPT's
        # "Open conversation" does not fit the width Claude's "Open pane" needs.
        import tkinter.font as tkfont
        font = tkfont.Font(font=(self.font_ui, 10))
        width = max(font.measure(self._T(key)) for key in ("open", "sidebar", "col_source")) + 24
        self.tree_chats.column("source", width=max(110, width))
        self._render_chats()
        self._render_log()
        self._rebuild_window_combo()
        self._render_state()
        self._render_usage()
        self._update_caption()

    # ------------------------------------------------------------- option help
    HELP_DELAY_MS = 300

    def _help_icon(self, parent, key):
        """A round "?" explaining an option in plain words, on hover, click or Tab focus."""
        import tkinter.font as tkfont
        t = THEME
        size = tkfont.Font(font=(self.font_ui, 10)).metrics("linespace")
        icon = tk.Canvas(parent, width=size, height=size, bg=t["panel"], takefocus=1,
                         highlightthickness=1, highlightbackground=t["panel"],
                         highlightcolor=t["amber"], cursor="question_arrow")
        ring = icon.create_oval(2, 2, size - 2, size - 2, outline=t["muted"], width=1)
        mark = icon.create_text(size / 2, size / 2, text="?", fill=t["muted"],
                                font=(self.font_ui, 8, "bold"))

        def lit(on):
            color = t["amber"] if on else t["muted"]
            icon.itemconfigure(ring, outline=color)
            icon.itemconfigure(mark, fill=color)

        def enter(_event):
            lit(True)
            self._cancel_help_timer()
            self._help_timer = self.after(self.HELP_DELAY_MS, lambda: self._show_help(key))

        def leave(_event):
            lit(icon.focus_get() is icon)
            if icon.focus_get() is not icon:
                self._hide_help()

        def focus_in(_event):
            lit(True)
            self._show_help(key)

        def focus_out(_event):
            lit(False)
            self._hide_help()

        icon.bind("<Enter>", enter)
        icon.bind("<Leave>", leave)
        icon.bind("<FocusIn>", focus_in)
        icon.bind("<FocusOut>", focus_out)
        icon.bind("<Button-1>", lambda _e: self._show_help(key))
        for sequence in ("<Return>", "<space>"):
            icon.bind(sequence, lambda _e: self._toggle_help(key))
        icon.bind("<Escape>", lambda _e: self._hide_help())
        self.help_icons[key] = icon
        return icon

    def _cancel_help_timer(self):
        if self._help_timer is not None:
            self.after_cancel(self._help_timer)
            self._help_timer = None

    def _show_help(self, key):
        self._cancel_help_timer()
        t = THEME
        if self.help_tip is None:
            self.help_tip = tk.Toplevel(self)
            self.help_tip.withdraw()
            self.help_tip.wm_overrideredirect(True)
            self.help_tip.attributes("-topmost", True)
            border = tk.Frame(self.help_tip, bg=t["border"])
            border.pack()
            self.help_label = tk.Label(border, justify="left", anchor="w", wraplength=380,
                                       bg=t["panel_hi"], fg=t["text"], font=(self.font_ui, 9),
                                       padx=12, pady=9)
            self.help_label.pack(padx=1, pady=1)
        self.help_label.config(text=self._T(key + "_help"))
        self.help_key = key
        # Beside the icon, kept inside the app window (which may be on any monitor).
        icon = self.help_icons[key]
        self.help_tip.update_idletasks()
        width, height = self.help_tip.winfo_reqwidth(), self.help_tip.winfo_reqheight()
        right = self.winfo_rootx() + self.winfo_width()
        bottom = self.winfo_rooty() + self.winfo_height()
        x = min(icon.winfo_rootx() + icon.winfo_width() + 8, right - width - 8)
        y = icon.winfo_rooty() + icon.winfo_height() + 6
        if y + height > bottom:
            y = icon.winfo_rooty() - height - 6
        self.help_tip.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.help_tip.deiconify()

    def _hide_help(self):
        self._cancel_help_timer()
        self.help_key = None
        if self.help_tip is not None:
            self.help_tip.withdraw()

    def _toggle_help(self, key):
        if self.help_key == key:
            self._hide_help()
        else:
            self._show_help(key)
        return "break"

    # ----------------------------------------------------------------- events
    def _toggle_chat(self, event):
        if event.keysym in ("space", "Return"):
            row = self.tree_chats.focus()
        elif self.tree_chats.identify_region(event.x, event.y) in ("cell", "tree"):
            row = self.tree_chats.identify_row(event.y)
        else:
            return          # heading (sorting), column separator or empty space
        if not row or int(row) >= len(self.view_rows):
            return
        key = self.view_rows[int(row)]["key"]
        if not self.checked_chats:
            # The first click turns what the list shows as watched (the open conversations)
            # into checks, so only the clicked row changes.
            self.checked_chats = {r["key"] for r in self.chat_rows if self._watched_by_default(r)}
        self.checked_chats ^= {key}
        self._push_config()
        self._render_chats()
        return "break"

    @staticmethod
    def _watched_by_default(row):
        """With nothing checked, the conversations open in the app are the ones watched."""
        return row.get("source") == "open" and row.get("available", False)

    def _sort_by(self, column):
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column, self.sort_descending = column, False
        self._render_chats()

    def _default_order(self):
        """Back to Claude's own order: newest conversation first."""
        self.sort_column, self.sort_descending = None, False
        self._render_chats()

    def _row_cells(self, row):
        """Displayed (watch, title, source, status) plus sort keys for one row."""
        status = self._T(row.get("phase", "watching"))
        if row.get("notice") == "unavailable":
            status = self._T("unavailable")
        elif not row.get("available", True):
            status = self._T("not_visible")
        due = row.get("due") if row.get("phase") in ("waiting", "verifying") else None
        checked = (row["key"] in self.checked_chats if self.checked_chats else
                   self._watched_by_default(row))
        cells = (("Tak" if checked else "Nie") if self.lang == "pl" else ("Yes" if checked else "No"),
                 row["title"], self._T(row.get("source", "sidebar")),
                 status + (f" · {due:%H:%M:%S}" if due else ""))
        keys = dict(watch=(not checked,), chat=(row["title"].casefold(),), source=(cells[2].casefold(),),
                    status=(status.casefold(), due or dt.datetime.max))
        return cells, keys

    def _render_chats(self, rows=None):
        focused = self.tree_chats.focus()
        focused_key = (self.view_rows[int(focused)]["key"]
                       if focused and int(focused) < len(self.view_rows) else None)
        if rows is not None:
            self.chat_rows = rows
        view = [(row, *self._row_cells(row)) for row in self.chat_rows]
        if self.sort_column:
            # Stable sort: equal rows keep Claude's newest-first order.
            view.sort(key=lambda item: item[2][self.sort_column], reverse=self.sort_descending)
        self.view_rows = [row for row, _, _ in view]
        scroll = self.tree_chats.yview()
        self.tree_chats.delete(*self.tree_chats.get_children())
        for i, (row, cells, _) in enumerate(view):
            self.tree_chats.insert("", "end", iid=str(i), values=cells)
            if row["key"] == focused_key:
                self.tree_chats.focus(str(i))
                self.tree_chats.selection_set(str(i))
        if scroll:
            self.tree_chats.yview_moveto(scroll[0])
        for col in ("watch", "chat", "source", "status"):
            arrow = (" ▼" if self.sort_descending else " ▲") if col == self.sort_column else ""
            self.tree_chats.heading(col, text=self._T("col_" + col) + arrow)
        self.btn_default_order.config(state="normal" if self.sort_column else "disabled")
        picked = bool(self.checked_chats)
        self.lbl_watching.config(text=self._T("watch_picked", n=len(self.checked_chats)) if picked
                                 else self._T("watch_open"))
        self.lbl_chats_hint.config(text=self._T("chats_empty") if not self.chat_rows else
                                   self._T("chats_hint_picked" if picked else "chats_hint_open"))

    def _on_window_selected(self, _event):
        idx = self.cmb_windows.current()
        if 0 <= idx < len(self.windows):
            self.worker.command("select_window", self.windows[idx][0])

    def _on_start(self):
        self._push_config()
        self.worker.command("start")

    def _on_stop(self):
        self.worker.command("stop")

    def _on_send_now(self):
        self._push_config()
        if messagebox.askyesno(self._T("dlg_send_title"), self._T("dlg_send_body")):
            self.worker.command("send_now")

    def _on_arm_manual(self):
        raw = self.ent_manual.get().strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m:
            messagebox.showerror(self._T("dlg_time_title"),
                                 self._T("dlg_time_format"))
            return
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            messagebox.showerror(self._T("dlg_time_title"),
                                 self._T("dlg_time_range"))
            return
        t = dt.datetime.now().replace(hour=h, minute=mi, second=0, microsecond=0)
        if t <= dt.datetime.now():
            t += dt.timedelta(days=1)
        self._push_config()
        self.worker.command("arm_manual", t)

    # ------------------------------------------------------------ message box
    MSG_LINES_MIN, MSG_LINES_MAX = 2, 20

    def _msg_lines(self):
        try:
            n = int(self.cfg.get("message_box_lines", 4))
        except (TypeError, ValueError):
            n = 4
        return max(self.MSG_LINES_MIN, min(self.MSG_LINES_MAX, n))

    def _draw_grip(self):
        self.grip_msg.delete("all")
        x = self.grip_msg.winfo_width() // 2
        for y in (3, 6):
            self.grip_msg.create_line(x - 16, y, x + 16, y,
                                      fill=THEME["muted"], width=1)

    def _on_grip_press(self, e):
        import tkinter.font as tkfont
        lh = tkfont.Font(font=self.txt_message.cget("font")).metrics("linespace")
        self._grip_origin = (e.y_root, int(self.txt_message.cget("height")),
                             max(1, lh))

    def _on_grip_drag(self, e):
        if not getattr(self, "_grip_origin", None):
            return
        y0, lines0, lh = self._grip_origin
        n = lines0 + int(round((e.y_root - y0) / lh))
        n = max(self.MSG_LINES_MIN, min(self.MSG_LINES_MAX, n))
        if n != int(self.txt_message.cget("height")):
            self.txt_message.configure(height=n)

    def _on_grip_release(self):
        self._grip_origin = None
        self.cfg["message_box_lines"] = int(self.txt_message.cget("height"))
        self._save_config()

    def _message_text(self):
        """Box content as a single line. Enter sends in Claude's composer, so a
        line break inside the message would submit it half-written."""
        return " ".join(self.txt_message.get("1.0", "end-1c").split()) or DEFAULT_MESSAGE

    def _commit_message(self):
        """Save the custom message; fall back to the default one when left blank."""
        msg = self._message_text()
        if msg != self.txt_message.get("1.0", "end-1c"):
            self.txt_message.delete("1.0", "end")
            self.txt_message.insert("1.0", msg)
        self._push_config()

    def _pick_plan_folder(self):
        """Point at the folder where the next sections of the project are written."""
        from tkinter import filedialog
        folder = filedialog.askdirectory(parent=self, initialdir=self.var_plan.get() or None,
                                         title=self._T("lbl_plan"))
        if folder:
            self.var_plan.set(os.path.normpath(folder))
            self._push_config()

    def _edit_handover_texts(self):
        """Edit both handover messages. The technical part (the file name, the marker and
        the paths) is added by the program, so it cannot be edited away."""
        t = THEME
        top = tk.Toplevel(self)
        top.title(self._T("dlg_handover_title"))
        top.configure(bg=t["panel"])
        top.transient(self)
        tk.Label(top, text=self._T("dlg_handover_intro"), bg=t["panel"], fg=t["muted"], anchor="w",
                 justify="left", wraplength=700, font=(self.font_ui, 9)).pack(fill="x", padx=12, pady=(12, 0))
        boxes = {}
        for key, label in (("handover_request_text", "dlg_handover_request"),
                           ("handover_continue_text", "dlg_handover_continue")):
            tk.Label(top, text=self._T(label), bg=t["panel"], fg=t["text"], anchor="w",
                     font=(self.font_ui, 10)).pack(fill="x", padx=12, pady=(12, 2))
            box = tk.Text(top, height=7, width=90, wrap="word", font=(self.font_ui, 10),
                          bg=t["log_bg"], fg=t["text"], insertbackground=t["text"],
                          relief="flat", padx=8, pady=6)
            box.pack(fill="both", expand=True, padx=12)
            box.insert("1.0", self.cfg.get(key) or self._T(key.replace("_text", "_default"),
                                                           context="…"))
            boxes[key] = box
        row = tk.Frame(top, bg=t["panel"])
        row.pack(fill="x", padx=12, pady=10)

        def defaults():
            for key, box in boxes.items():
                box.delete("1.0", "end")
                box.insert("1.0", self._T(key.replace("_text", "_default"), context="…"))

        def save():
            for key, box in boxes.items():
                text = " ".join(box.get("1.0", "end-1c").split())
                default = " ".join(self._T(key.replace("_text", "_default"), context="…").split())
                self.cfg[key] = "" if text == default else text
            self._push_config()
            top.destroy()

        ttk.Button(row, text=self._T("btn_defaults"), command=defaults).pack(side="left")
        ttk.Button(row, text=self._T("btn_save"), command=save).pack(side="right")
        top.bind("<Escape>", lambda _e: top.destroy())
        # Over this window (not in a corner of the screen), with the keyboard in the first
        # message, so typing and Escape work at once.
        top.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - top.winfo_reqwidth()) // 2)
        top.geometry(f"+{max(0, x)}+{max(0, self.winfo_rooty() + 60)}")
        enable_dark_titlebar(top)
        boxes["handover_request_text"].focus_set()     # the dialog's keyboard focus...
        boxes["handover_request_text"].focus_force()   # ...and the dialog in front with it

    def _push_config(self):
        try:
            interval = max(5, int(self.var_interval.get()))
        except (tk.TclError, ValueError):
            interval = DEFAULT_CONFIG["scan_interval_s"]
        try:
            start_delay = min(120, max(0, int(self.var_start_delay.get())))
        except (tk.TclError, ValueError):
            start_delay = DEFAULT_CONFIG["start_delay_s"]
        try:
            quiet = min(300, max(5, int(self.var_quiet.get())))
        except (tk.TclError, ValueError):
            quiet = DEFAULT_CONFIG["foreign_input_quiet_s"]
        # A threshold we cannot read is not saved: the previous one stays in force and the
        # hint beside the field says what the field takes.
        threshold = self.var_threshold.get().strip()
        if parse_threshold(threshold):
            self.lbl_threshold_hint.config(text=self._T("hint_threshold"), fg=THEME["muted"])
        else:
            threshold = self.cfg["handover_threshold"]
            self.lbl_threshold_hint.config(text=self._T("hint_threshold"), fg=THEME["red"])
        message = self._message_text()
        payload = {
            "scan_interval_s": interval,
            "start_delay_s": start_delay,
            "foreign_input_quiet_s": quiet,
            "handover_threshold": threshold,
            "plan_folder": self.var_plan.get().strip(),
            "handover_request_text": self.cfg.get("handover_request_text", ""),
            "handover_continue_text": self.cfg.get("handover_continue_text", ""),
            "auto_send": bool(self.var_autosend.get()),
            "keep_awake": bool(self.var_awake.get()),
            "message": message,
            "watch_scope": "selected" if self.checked_chats else "open",
            "selected_chats": sorted(self.checked_chats),
            **{key: bool(var.get()) for key, var in self.feature_vars.items()},
        }
        self.cfg.update(payload)
        self._save_config()
        self.worker.command("config", payload)
        self._show_handover_rows()
        self._render_chats()

    # ------------------------------------------------------------- worker queue
    def _poll_queue(self):
        try:
            while True:
                kind, data = self.out_queue.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _handle_event(self, kind, data):
        if kind == "chats":
            self._render_chats(data)
        elif kind == "selection":            # a handover replaced a checked conversation
            self.checked_chats = set(data)
            self.cfg["selected_chats"] = sorted(self.checked_chats)
            self._render_chats()
        elif kind == "log":
            full = len(self.log_lines) == self.log_lines.maxlen
            self.log_lines.append(data)
            self.txt_log.configure(state="normal")
            if full:                               # the oldest line leaves the window too
                self.txt_log.delete("1.0", "2.0")
            self._insert_log_line(*data)
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        elif kind == "windows":
            self.windows = data
            self._rebuild_window_combo()
            if data:
                index = next((i for i, (hwnd, _) in enumerate(data) if hwnd == self.worker.hwnd), 0)
                self.cmb_windows.current(index)
            if not data:
                self.last_status = "no_window"
                self._update_caption()
        elif kind == "usage":
            self.usage.update(data)
            self._render_usage()
        elif kind == "status":
            self.last_status = data
        elif kind in ("state", "countdown"):
            prev = self.state_info.get("state")
            self.state_info = data
            if data["state"] in ("ARMED", "STARTING") and prev != data["state"]:
                self.armed_since = dt.datetime.now()
            elif data["state"] not in ("ARMED", "STARTING"):
                self.armed_since = None
            self._render_state()
        elif kind == "beep":
            try:
                import winsound
                winsound.MessageBeep()
            except Exception:
                pass

    # --------------------------------------------------------------------- log
    def _insert_log_line(self, line, level):
        text = self._T(line.key, **line.kw) if isinstance(line, Text) and line.key else str(line)
        self.txt_log.insert("end", text + "\n", (level,) if level in ("warn", "good", "bad") else ())

    def _render_log(self):
        """Every kept line again, in the window's current language."""
        at_end = self.txt_log.yview()[1] >= 0.999
        top = self.txt_log.yview()[0]
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        for line, level in self.log_lines:
            self._insert_log_line(line, level)
        self.txt_log.configure(state="disabled")
        if at_end:
            self.txt_log.see("end")
        else:
            self.txt_log.yview_moveto(top)

    # ------------------------------------------------------------------ drawing
    def _rebuild_window_combo(self):
        vals = [self._T("combo_handle", title=title, hwnd=hwnd)
                for hwnd, title in self.windows]
        keep = self.cmb_windows.current()
        self.cmb_windows["values"] = vals
        if 0 <= keep < len(vals):
            self.cmb_windows.current(keep)

    @staticmethod
    def _usage_bar(canvas, fill_id, pct):
        """Fill a small meter by the percentage USED (red when used up)."""
        width = canvas.winfo_width() or 90
        frac = max(0, min(100, pct)) / 100
        color = (THEME["red"] if pct >= 100
                 else THEME["amber"] if pct >= 80 else THEME["green"])
        canvas.coords(fill_id, 0, 0, int(width * frac), 5)
        canvas.itemconfigure(fill_id, fill=color)

    def _render_usage(self):
        rows = self.usage.get("rows", {})
        bar = self._usage_bar
        h5 = rows.get("5h", {})
        wk = rows.get("weekly", {})
        fb = rows.get("weekly_fable", {})

        if "pct" in h5:
            self.lbl_used.config(text=self._T("usage_5h", pct=h5["pct"]))
            bar(self.bar_used, self.bar_used_fill, h5["pct"])
        else:
            self.lbl_used.config(text=self._T("usage_5h_none"))
            self.bar_used.coords(self.bar_used_fill, 0, 0, 0, 5)

        if "pct" in wk:
            self.lbl_plan.config(text=self._T("usage_weekly", pct=wk["pct"]))
            bar(self.bar_plan, self.bar_plan_fill, wk["pct"])
        else:
            self.lbl_plan.config(text="")
            self.bar_plan.coords(self.bar_plan_fill, 0, 0, 0, 5)

        parts = []
        if "pct" in fb:
            parts.append(self._T("usage_fable", pct=fb["pct"]))
        if "reset" in h5:
            parts.append(self._T("usage_5h_reset", reset=f"{h5['reset']:%a %H:%M}"))
        self.lbl_reset_seen.config(text="  ·  ".join(parts))

    def _start_seconds_left(self):
        until = self.state_info.get("start_until")
        return max(0, math.ceil((until - dt.datetime.now()).total_seconds())) if until else 0

    def _render_state(self):
        st = self.state_info["state"]
        running = st != "IDLE"
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")
        why = self._T("pause_" + (self.state_info.get("pause_reason") or "user"))
        self.lbl_state.config(text=self._T(STATE_LABEL[st], sec=self._start_seconds_left(), why=why))
        self.lamp.itemconfigure(self.lamp_id, fill=THEME[STATE_COLOR[st]])

    def _update_caption(self):
        """Set the header caption from current state (language-aware)."""
        st = self.state_info["state"]
        now = dt.datetime.now()
        if st == "IDLE":
            self.lbl_caption.config(text=self._T("cap_click_start"))
        elif st == "STARTING":
            self.lbl_caption.config(text=self._T("cap_starting"))
        elif st == "MONITORING":
            if self.last_status == "no_window":
                self.lbl_caption.config(text=self._T("cap_no_window"))
            elif self.last_status == "hidden":
                self.lbl_caption.config(text=self._T("cap_hidden"))
            elif self.usage.get("session"):
                self.lbl_caption.config(
                    text=self._T("cap_guarding",
                                 session=self.usage["session"][:60]))
            else:
                self.lbl_caption.config(
                    text=self._T("cap_scanning", sec=self.cfg["scan_interval_s"]))
        elif st == "ARMED":
            send_at = self.state_info.get("send_at")
            if send_at:
                reset_at = self.state_info.get("reset_at")
                if reset_at:
                    self.lbl_caption.config(
                        text=self._T("cap_armed_reset",
                                     reset=f"{reset_at:%a %H:%M}",
                                     send=f"{send_at:%H:%M:%S}"))
                else:
                    self.lbl_caption.config(
                        text=self._T("cap_armed_noreset",
                                     send=f"{send_at:%H:%M:%S}"))
        elif st == "PAUSED":
            self.lbl_caption.config(text=self._T("cap_paused"))
        elif st == "VERIFY":
            self.lbl_caption.config(text=self._T("cap_verify"))

    def _tick_ui(self):
        st = self.state_info["state"]
        now = dt.datetime.now()
        t = THEME

        if st == "IDLE":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["muted"])
        elif st == "STARTING":
            left = self._start_seconds_left()
            self.lbl_clock.config(text=f"00:{left // 60:02d}:{left % 60:02d}", fg=t["amber"])
            self.lbl_state.config(text=self._T("state_starting", sec=left))
            self.lamp.itemconfigure(
                self.lamp_id, fill=t["amber"] if int(time.time() * 2) % 2 == 0 else t["amber_dim"])
        elif st == "MONITORING":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["text"])
        elif st == "ARMED":
            send_at = self.state_info.get("send_at")
            if send_at:
                secs = max(0, int((send_at - now).total_seconds()))
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                self.lbl_clock.config(text=f"{h:02d}:{m:02d}:{s:02d}", fg=t["amber"])
            blink_on = int(time.time()) % 2 == 0
            self.lamp.itemconfigure(
                self.lamp_id, fill=t["amber"] if blink_on else t["amber_dim"])
        elif st == "VERIFY":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["blue"])

        self._update_caption()
        self._draw_progress()
        self.after(250, self._tick_ui)

    def _draw_progress(self):
        width = self.progress.winfo_width() or 1
        frac = 0.0
        st = self.state_info["state"]
        target = self.state_info.get("start_until" if st == "STARTING" else "send_at")
        if (st in ("ARMED", "STARTING") and target and self.armed_since
                and target > self.armed_since):
            total = (target - self.armed_since).total_seconds()
            done = (dt.datetime.now() - self.armed_since).total_seconds()
            frac = max(0.0, min(1.0, done / total))
        self.progress.coords(self.progress_fill, 0, 0, int(width * frac), 4)

    def _on_close(self):
        self.worker.shutdown()
        allow_sleep()
        self.destroy()


def window_geometry(argv):
    """`--geometry WxH+X+Y` (size optional): where the launcher asked to open the
    window, e.g. side by side with the other Auto-Resume. None when absent."""
    for i, arg in enumerate(argv):
        value = arg.split("=", 1)[1] if arg.startswith("--geometry=") else (
            argv[i + 1] if arg == "--geometry" and i + 1 < len(argv) else None)
        if value and re.fullmatch(r"(?:\d+x\d+)?[+-]\d+[+-]\d+", value):
            return value
    return None


def main(app_class=None, argv=None):
    # crisp DPI on Windows 11
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    app = (app_class or App)()
    geometry = window_geometry(sys.argv[1:] if argv is None else argv)
    if geometry:
        app.geometry(geometry)
    app.mainloop()


if __name__ == "__main__":
    main()
