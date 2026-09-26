"""Draw the README's screenshots: the picker and both Auto-Resume windows, filled with
sample conversations, so no chat of yours ends up on GitHub.

Nothing is read from Claude or ChatGPT, nothing is clicked or typed, and neither your
settings nor your logs are touched. Needs Pillow:  py tools/readme_screenshots.py
"""
import ctypes
import ctypes.wintypes
import datetime as dt
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import ImageGrab  # noqa: E402

import chatgpt_auto_continue as gpt  # noqa: E402
import claude_auto_continue as app  # noqa: E402
import launcher  # noqa: E402
from strings import Text  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
NOW = dt.datetime.now().replace(microsecond=0)


def capture(window, name, above=None):
    """The window as the screen shows it (title bar included, no shadow); `above` is a
    tooltip that has to stay in front of it."""
    window.attributes("-topmost", True)
    window.lift()
    if above is not None:
        above.lift()
    for _ in range(10):
        window.update()
        time.sleep(0.05)
    hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
    rect = ctypes.wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
    path = os.path.join(DOCS, name)
    image.save(path, optimize=True)
    window.attributes("-topmost", False)
    print(path, image.size)


def fit(ui):
    """As tall as the page, so the log shows in full and nothing is left empty below it."""
    ui.update_idletasks()
    ui.geometry("860x%d+80+20" % ui.page_canvas.bbox("all")[3])


def close(window):
    """Destroy the window without leaving its timers running."""
    for timer in window.tk.call("after", "info"):
        window.after_cancel(timer)
    window.destroy()


def log(ui, minutes_ago, key, level="info", **kw):
    """A log line as the worker writes it, stamped `minutes_ago` minutes back."""
    stamp = f"{NOW - dt.timedelta(minutes=minutes_ago):%Y-%m-%d %H:%M:%S}"
    msg = ui.worker.text(key, **kw)
    ui._handle_event("log", (Text(ui.worker.t("log_line", stamp=stamp, msg=msg), "log_line",
                                  dict(stamp=stamp, msg=msg)), level))


def row(kind, title, source, phase="inactive", due=None):
    return dict(key=f"{kind}:{title}", title=title, kind=kind, source=source, available=True,
                phase=phase, due=due, attempts=0, notice="")


def armed(ui, reset, send, since):
    ui._handle_event("state", dict(state="ARMED", reset_at=reset, send_at=send, start_until=None,
                                   pause_reason=None))
    ui.armed_since = since
    ui._tick_ui()


def claude_windows():
    reset = NOW + dt.timedelta(minutes=26)
    send = reset + dt.timedelta(minutes=1)
    ui = app.App()
    ui._handle_event("windows", [(67590, "Claude")])
    ui._handle_event("chats", [
        row("code", "Refactor the billing module", "open", "waiting", send),
        row("code", "Write the onboarding tests", "open", "watching"),
        row("chat", "Plan the Q4 roadmap", "sidebar"),
        row("code", "Fix the flaky CI job", "sidebar"),
        row("chat", "Summarize the research notes", "sidebar"),
    ])
    ui._handle_event("usage", {"rows": {"5h": {"pct": 100, "reset": reset}, "weekly": {"pct": 48}},
                               "session": "Refactor the billing module"})
    log(ui, 212, "log_starting", "warn", sec=10)
    log(ui, 212, "log_started")
    log(ui, 131, "log_chat_action", chat="Write the onboarding tests", action=ui.worker.text("approach_submitted"))
    log(ui, 94, "log_paused", "warn", why=ui.worker.text("pause_user"))
    log(ui, 93, "log_pause_over")
    log(ui, 37, "log_chat_action", chat="Refactor the billing module",
        action=ui.worker.text("waiting_limit_at", reset=f"{reset:%H:%M}", send=f"{send:%H:%M:%S}"))
    armed(ui, reset, send, NOW - dt.timedelta(minutes=37))
    fit(ui)
    capture(ui, "screenshot.png")

    # Settings, with the handover on and one "?" open
    ui.notebook.select(ui.settings_tab)
    ui.var_plan.set(r"C:\Projects\billing\docs\plan")
    ui.update()
    ui._show_help("plan_folder")
    capture(ui, "settings.png", above=ui.help_tip)
    ui._hide_help()

    # The two handover messages, with what they are for
    ui._edit_handover_texts()
    dialog = next(w for w in ui.winfo_children() if isinstance(w, app.tk.Toplevel) and w is not ui.help_tip)
    capture(dialog, "messages.png")
    dialog.destroy()
    close(ui)


def chatgpt_window():
    reset = NOW + dt.timedelta(minutes=41)
    send = reset + dt.timedelta(minutes=1)
    ui = gpt.ChatGPTApp()
    ui._handle_event("windows", [(24316084, "ChatGPT")])
    ui._handle_event("chats", [
        row("codex", "Build the export endpoint", "open", "waiting", send),
        row("codex", "Add retries to the sync job", "sidebar"),
        row("codex", "Migrate the settings page", "sidebar"),
        row("codex", "Write the release notes", "sidebar"),
    ])
    ui._handle_event("usage", {"rows": {"5h": {"pct": 100, "left": 0, "reset": reset},
                                        "weekly": {"pct": 38, "left": 62}}})
    log(ui, 58, "log_starting", "warn", sec=10)
    log(ui, 58, "log_started")
    rows = Text.joined(", ", [ui.worker.text("usage_row", window=ui.worker.text(w), left=left)
                              for w, left in (("window_5h", 0), ("window_weekly", 62))])
    log(ui, 19, "log_usage_left", rows=rows)
    log(ui, 19, "log_chat_action", chat="Build the export endpoint",
        action=ui.worker.text("waiting_limit_at", reset=f"{reset:%H:%M}", send=f"{send:%H:%M:%S}"))
    armed(ui, reset, send, NOW - dt.timedelta(minutes=19))
    fit(ui)
    capture(ui, "chatgpt.png")
    close(ui)


def picker():
    window = launcher.Launcher()
    window.geometry("+80+40")
    capture(window, "launcher.png")
    close(window)


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    config = dict(app.DEFAULT_CONFIG, language="en", retry_api_errors=True, auto_approach=True,
                  pause_on_foreign_input=True, handover_enabled=True)
    folder = tempfile.mkdtemp()
    with patch.object(app, "load_config", lambda path=None, defaults=None: dict(config)), \
            patch.object(app, "save_config", lambda cfg, path=None: None), \
            patch.object(app, "LOG_PATH", os.path.join(folder, "claude.log")), \
            patch.object(gpt, "LOG_PATH", os.path.join(folder, "chatgpt.log")), \
            patch.object(app.MonitorWorker, "start", lambda worker: None):
        picker()
        claude_windows()
        chatgpt_window()


if __name__ == "__main__":
    main()
