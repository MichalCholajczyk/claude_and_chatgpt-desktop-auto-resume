# -*- coding: utf-8 -*-
"""
ChatGPT Auto-Resume — auto-continue for the ChatGPT desktop app (Windows 11).

The ChatGPT counterpart of Claude Auto-Resume, sharing its window, settings and
scheduler. It watches the ChatGPT app (Codex mode) through UI Automation, notices
the "You've hit your usage limit" message, checks the remaining limit the way a
person would — user button in the bottom-left corner -> "Usage remaining" — and,
a minute after the limit resets, types the message to send into the stopped
conversation.

Requires: Python 3.10+, package `uiautomation` (pip install uiautomation).
"""
import copy
import ctypes
import ctypes.wintypes
import datetime as dt
import os
import re
import time

import tkinter as tk
from tkinter import ttk

import claude_auto_continue as app
from chatgpt_automation import USAGE_CHECK_S, ChatGPTEngine
from chatgpt_limits import PERCENT, parse_usage_rows

auto, user32 = app.auto, app.user32

CONFIG_PATH = os.path.join(app.APP_DIR, "chatgpt_auto_continue_config.json")
LOG_PATH = os.path.join(app.APP_DIR, "chatgpt_auto_continue.log")

# Codex's context window is about 258k tokens, so a threshold in tokens borrowed from
# Claude (700k) would never be reached: here the default is a share of the window.
DEFAULT_CONFIG = dict(app.DEFAULT_CONFIG, handover_threshold="70%")

# The ChatGPT app ships as ChatGPT.exe (also started as Codex.exe) in its Store package.
CHATGPT_EXES = ("chatgpt.exe", "codex.exe")
PROFILE_BUTTON = {"open profile menu", "otwórz menu profilu"}
USAGE_HEADING = ("usage remaining", "pozostały limit")
# Some accounts get a plain "Usage · 40% left" item instead of the expandable rows.
USAGE_ITEM = re.compile(r"^(?:Usage|Użycie)\b.*?(?:(\d{1,3})\s*%\s*left|Pozostało\s*(\d{1,3})\s*%)", re.I)

# ------------------------------------------------------------------- strings


def _chatgpt_strings():
    """Claude Auto-Resume's texts with ChatGPT's name and ChatGPT's own details."""
    table = copy.deepcopy(app.STRINGS)
    for lang in table.values():
        for key, value in lang.items():
            lang[key] = value.replace("Claude", "ChatGPT")
    table["en"].update({
        # The blanket rename above would turn Claude's own computer use into ChatGPT's.
        "pause_claude_cu": "Claude is using the computer",
        "open": "Open conversation",
        "watch_open": "Watching the conversation open in ChatGPT",
        "chats_hint_open": ("Click a row to choose the conversations yourself — then only the checked ones "
                            "are watched. ChatGPT lists Codex and Chat conversations separately: switch its "
                            "mode, open older chats, then click “Read chats”."),
        "chats_hint_picked": ("Click a row to check or uncheck it. Uncheck them all to go back to the "
                              "conversation open in ChatGPT."),
        "chats_empty": "Click “Read chats” to list the conversation open in ChatGPT and the chats in its sidebar.",
        "retry": "Clicked Retry",
        "usage_5h_left": "5-hour limit: {left}% left",
        "usage_weekly_left": "weekly: {left}% left",
        "log_panel_unreadable": "Couldn’t read “Usage remaining” in ChatGPT’s profile menu — will try again.",
        "log_usage_left": "Usage remaining: {rows}",
        # the limit windows by our own names, never by ChatGPT's (which follow its language)
        "usage_row": "{window} {left}%", "window_raw": "{label}", "window_hours": "{n}-hour",
        "window_5h": "5-hour", "window_daily": "daily", "window_weekly": "weekly", "window_monthly": "monthly",
        "log_no_open_chat": "No open conversation found in the ChatGPT window — open the one to watch "
                            "(or check chats under Conversations).",
        "blocked_no_notice": "limit used up, but no stop notice seen here (last text: “{seen}”)",
        "lbl_usage_check": "Check remaining limit every",
        "lbl_usage_check_unit": "min (0 = only after a limit)",
        "retry_api_errors_help": (
            "When ChatGPT stops because of a temporary problem on its side (busy servers, "
            "“An error occurred”, a dropped connection), the program tries again by itself: it clicks "
            "“Retry” or sends your message.\n\n"
            "First retry after 30 s, each next one later (at most every 15 min), up to 6 times. "
            "Account and access errors (e.g. signed out) are not retried — they need you. "
            "Works only with “Send automatically” on."),
    })
    table["pl"].update({
        "pause_claude_cu": "Claude używa komputera",
        "open": "Otwarta rozmowa",
        "watch_open": "Pilnuję rozmowy otwartej w ChatGPT",
        "chats_hint_open": ("Kliknij wiersz, aby samodzielnie wybrać rozmowy — wtedy pilnuję tylko "
                            "zaznaczonych. ChatGPT pokazuje osobno rozmowy Codex i Chat: przełącz tryb, "
                            "otwórz starszy czat i kliknij „Odczytaj czaty”."),
        "chats_hint_picked": ("Kliknij wiersz, aby go zaznaczyć lub odznaczyć. Odznacz wszystkie, by wrócić "
                              "do rozmowy otwartej w ChatGPT."),
        "chats_empty": "Kliknij „Odczytaj czaty”, aby zobaczyć rozmowę otwartą w ChatGPT i czaty z jego paska bocznego.",
        "retry": "Kliknięto Ponów",
        "usage_5h_left": "limit 5-godzinny: pozostało {left}%",
        "usage_weekly_left": "tygodniowy: pozostało {left}%",
        "log_panel_unreadable": "Nie udało się odczytać „Pozostały limit” w menu profilu ChatGPT — spróbuję ponownie.",
        "log_usage_left": "Pozostały limit: {rows}",
        "usage_row": "{window} {left}%", "window_raw": "{label}", "window_hours": "{n}-godzinny",
        "window_5h": "5-godzinny", "window_daily": "dzienny", "window_weekly": "tygodniowy",
        "window_monthly": "miesięczny",
        "log_no_open_chat": "Nie widzę otwartej rozmowy w oknie ChatGPT — otwórz tę do pilnowania "
                            "(albo zaznacz czaty w zakładce Rozmowy).",
        "blocked_no_notice": "limit wyczerpany, ale nie widzę tu komunikatu o limicie (ostatni tekst: „{seen}”)",
        "lbl_usage_check": "Sprawdzaj pozostały limit co",
        "lbl_usage_check_unit": "min (0 = tylko po limicie)",
        "retry_api_errors_help": (
            "Gdy ChatGPT przerwie pracę przez chwilowy błąd po swojej stronie (zajęte serwery, "
            "„Wystąpił błąd”, zerwane połączenie), program sam spróbuje ponownie: kliknie „Ponów” "
            "albo wyśle Twoją wiadomość.\n\n"
            "Pierwsza próba po 30 s, każda kolejna później (maks. co 15 min), najwyżej 6 razy. "
            "Błędów konta i dostępu (np. wylogowanie) nie ponawia — wtedy potrzebna jest Twoja reakcja. "
            "Działa tylko z włączonym „Wysyłaj automatycznie”."),
    })
    return table


STRINGS = _chatgpt_strings()

# -------------------------------------------------------------- monitor thread


class ChatGPTWorker(app.MonitorWorker):
    strings = STRINGS
    engine_class = ChatGPTEngine

    def log_path(self):
        return LOG_PATH

    def _enum_windows(self):
        """(hwnd, title) of ChatGPT app windows, the main (largest) one first."""
        found = []
        try:
            for w in auto.GetRootControl().GetChildren():
                try:
                    if w.ClassName != "Chrome_WidgetWin_1" or app.click_through(w.NativeWindowHandle):
                        continue
                    if app.process_exe_name(w.ProcessId) not in CHATGPT_EXES:
                        continue
                    hwnd, r = w.NativeWindowHandle, w.BoundingRectangle
                    width, height = r.right - r.left, r.bottom - r.top
                    if not user32.IsIconic(hwnd) and (width < 400 or height < 300):
                        continue      # the floating pet or a quick-chat bubble
                    found.append((width * height, hwnd, w.Name or "ChatGPT"))
                except Exception:
                    continue
        except Exception:
            pass
        found.sort(key=lambda item: -item[0])
        return [(hwnd, title) for _, hwnd, title in found]

    # ------------------------------------------- "Usage remaining" (physical check)
    def _read_usage_menu(self, win):
        """Check the remaining limit like a person would: click the user button in the
        bottom-left corner, expand "Usage remaining", read its rows, close the menu with
        Escape, put the mouse cursor back and give the focus back to the window that
        had it. Returns (rows, ok)."""
        if win is None:
            return [], False
        ui = self.engine.ui
        try:
            buttons = [n for n in ui.snapshot().walk() if n.type == "ButtonControl"
                       and n.name.strip().casefold() in PROFILE_BUTTON and n.visible]
        except Exception:
            return [], False
        if len(buttons) != 1:
            return [], False          # sidebar hidden, or not the ChatGPT we know
        if self.guard.user_busy():
            return [], False          # someone else has the mouse; the menu can wait
        previous = user32.GetForegroundWindow()
        self._focus_window(self.hwnd)
        try:
            if user32.GetForegroundWindow() != self.hwnd or not self.cmds.empty() or self._stop_event.is_set():
                return [], False
            cursor = ctypes.wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(cursor))
            rows = []
            try:
                button = buttons[0].control
                if button.IsOffscreen or not button.IsEnabled:
                    return [], False
                with self.guard.sending():
                    button.Click(simulateMove=False)
                rows = self._usage_rows()
            except Exception:
                rows = []
            finally:
                self._close_menu()
                with self.guard.sending():
                    user32.SetCursorPos(cursor.x, cursor.y)
            return rows, bool(rows)
        finally:
            if previous and previous != self.hwnd and user32.IsWindow(previous):
                self._focus_window(previous)

    def _find_menu(self):
        """(menu, heading item, plain usage item) of the open profile menu, or Nones."""
        for _ in range(6):
            time.sleep(0.3)
            try:
                root = self.engine.ui.snapshot()
            except Exception:
                continue
            for menu in (n for n in root.walk() if n.type == "MenuControl" or n.role == "menu"):
                items = [n for n in menu.walk() if n.type == "MenuItemControl"]
                heading = [n for n in items if n.name.strip().casefold().startswith(USAGE_HEADING)]
                plain = [n for n in items if USAGE_ITEM.match(n.name.strip())]
                if heading or plain:
                    return menu, (heading[0] if heading else None), (plain[0] if plain else None)
        return None, None, None

    @staticmethod
    def _menu_texts(menu, heading):
        """Texts after the "Usage remaining" item, outside other menu items."""
        texts, after = [], False

        def visit(node):
            nonlocal after
            for child in node.children:
                if child is heading:
                    after = True
                elif child.type == "MenuItemControl":
                    continue
                elif child.type == "TextControl" and child.name.strip():
                    if after:
                        texts.append(child.name.strip())
                else:
                    visit(child)
        visit(menu)
        return texts

    def _usage_rows(self):
        menu, heading, plain = self._find_menu()
        if menu is None:
            return []
        if heading is None:
            m = USAGE_ITEM.match(plain.name.strip())
            left = int(m.group(1) or m.group(2))
            return [dict(label="", minutes=None, left=min(100, left), reset=None, model=None)]
        texts = self._menu_texts(menu, heading)
        if not any(PERCENT.match(t) for t in texts):
            self.engine.ui.click(heading)          # expands the rows below it
            for _ in range(5):                     # they render a moment later
                menu, heading, _ = self._find_menu()
                if menu is None or heading is None:
                    return []
                texts = self._menu_texts(menu, heading)
                if any(PERCENT.match(t) for t in texts):
                    break
        return parse_usage_rows(texts, dt.datetime.now())

    def _close_menu(self):
        """Escape closes the whole menu. Never press it when no menu is open: in the
        composer Escape could interrupt ChatGPT."""
        for attempt in range(3):
            if user32.GetForegroundWindow() != self.hwnd:
                return
            try:
                root = self.engine.ui.snapshot()
                still_open = any(n.type == "MenuControl" or n.role == "menu" for n in root.walk())
            except Exception:
                still_open = attempt == 0      # we opened it a moment ago
            if not still_open:
                return
            with self.guard.sending():
                auto.SendKeys("{Esc}", waitTime=0.05)
            time.sleep(0.35)


# ------------------------------------------------------------------- window


class ChatGPTApp(app.App):
    TITLE = "ChatGPT Auto-Resume"
    WORDMARK = "CHATGPT AUTO-RESUME"
    WORKER = ChatGPTWorker
    # No "Try again" button after a limit in ChatGPT, and Codex's own questions skip
    # themselves after a countdown: only the API/server retry option applies.
    FEATURES = ("retry_api_errors", "pause_on_foreign_input", "handover_enabled")

    def _load_config(self):
        cfg = app.load_config(CONFIG_PATH, DEFAULT_CONFIG)
        try:
            cfg["usage_check_s"] = min(7200, max(0, int(cfg.get("usage_check_s", USAGE_CHECK_S))))
        except (TypeError, ValueError):
            cfg["usage_check_s"] = USAGE_CHECK_S
        return cfg

    def _save_config(self):
        app.save_config(self.cfg, CONFIG_PATH)

    def _build_ui(self):
        """Claude Auto-Resume's window plus how often to read "Usage remaining"."""
        super()._build_ui()
        t = app.THEME
        row = tk.Frame(self.settings_tab, bg=t["panel"])
        row.pack(fill="x", pady=(8, 0))
        self.lbl_usage_check = tk.Label(row, text="", bg=t["panel"], fg=t["text"], font=(self.font_ui, 10))
        self.lbl_usage_check.pack(side="left")
        self.var_usage_check = tk.IntVar(value=self.cfg["usage_check_s"] // 60)
        ttk.Spinbox(row, from_=0, to=120, width=4, textvariable=self.var_usage_check,
                    command=self._push_config).pack(side="left", padx=4)
        self.lbl_usage_check_unit = tk.Label(row, text="", bg=t["panel"], fg=t["text"], font=(self.font_ui, 10))
        self.lbl_usage_check_unit.pack(side="left")

    def _retext(self):
        super()._retext()
        self.lbl_usage_check.config(text=self._T("lbl_usage_check"))
        self.lbl_usage_check_unit.config(text=self._T("lbl_usage_check_unit"))

    def _push_config(self):
        try:
            minutes = min(120, max(0, int(self.var_usage_check.get())))
        except (tk.TclError, ValueError):
            minutes = USAGE_CHECK_S // 60
        self.cfg["usage_check_s"] = minutes * 60
        self.worker.command("config", {"usage_check_s": minutes * 60})
        super()._push_config()      # saves the settings, including the interval

    def _render_usage(self):
        """ChatGPT counts what is LEFT; the meters still fill up as the limit is used."""
        rows = self.usage.get("rows", {})
        h5, wk = rows.get("5h", {}), rows.get("weekly", {})
        if "left" in h5:
            self.lbl_used.config(text=self._T("usage_5h_left", left=h5["left"]))
            self._usage_bar(self.bar_used, self.bar_used_fill, h5["pct"])
        else:
            self.lbl_used.config(text=self._T("usage_5h_none"))
            self.bar_used.coords(self.bar_used_fill, 0, 0, 0, 5)
        if "left" in wk:
            self.lbl_plan.config(text=self._T("usage_weekly_left", left=wk["left"]))
            self._usage_bar(self.bar_plan, self.bar_plan_fill, wk["pct"])
        else:
            self.lbl_plan.config(text="")
            self.bar_plan.coords(self.bar_plan_fill, 0, 0, 0, 5)
        reset = h5.get("reset") if h5.get("left", 100) <= 0 else None
        self.lbl_reset_seen.config(text=self._T("usage_5h_reset", reset=f"{reset:%a %H:%M}") if reset else "")


def main(argv=None):
    app.main(ChatGPTApp, argv)


if __name__ == "__main__":
    main()
