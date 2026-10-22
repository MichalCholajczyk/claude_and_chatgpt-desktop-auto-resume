# -*- coding: utf-8 -*-
"""What Auto-Resume reads from the open Claude and ChatGPT windows, conversation by
conversation: the limit and wrap-up signals, the cards, the context and the settings the
conversation's bar shows. The first thing to run when a new app version changes its layout.

Read-only: nothing is clicked, typed or sent, and no conversation text is printed (titles,
button labels and the meter's own label are).

    py -3 tools/live_check.py            # both apps
    py -3 tools/live_check.py claude     # one of them
    py -3 tools/live_check.py chatgpt
"""
import os
import queue
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chatgpt_auto_continue as gpt                 # noqa: E402
import chatgpt_automation as gpt_ui                 # noqa: E402
import claude_auto_continue as app                  # noqa: E402
import session_automation as sa                     # noqa: E402
from codex_log import CodexLog                      # noqa: E402
from quota_log import QuotaLog                      # noqa: E402

SIGNALS = ("limit", "reset", "wrap_up", "card", "busy", "error")


def ascii_safe(text):
    return (text or "").encode("ascii", "replace").decode()


def flags(state):
    """The signals on one line. An error shows only that there is one, and whether it is
    permanent: its text can be the conversation's own words."""
    shown = dict(state)
    if shown.get("error"):
        shown["error"] = "permanent" if shown.get("permanent") else "yes"
    return "  ".join("%s=%s" % (k, ascii_safe(str(shown[k]))[:60]) for k in SIGNALS if k in shown)


def question(q):
    if not q:
        return "no"
    return "yes, %d options, %d recommended, main button %r" % (
        len(q["options"]), len(q["recommended"]), ascii_safe(q["submit"].name))


def next_step(box):
    if not box:
        return "no"
    return "yes, main button %r%s" % (ascii_safe(box["label"]), ", an app action left for you" if box["action"] else "")


def new_session(card):
    return "%s %r" % (card["kind"], ascii_safe(card["title"])) if card else "no"


def logged(reader, title):
    try:
        return reader.context_for(title)
    except Exception as exc:          # an unreadable log is reported, never fatal
        return "unreadable (%s)" % exc


def windows_of(worker, label):
    worker.log = lambda *a, **kw: None
    found = worker._enum_windows()
    if not found:
        print("%s: no window" % label)
    for hwnd, _title in found:
        worker.hwnd = hwnd
        try:
            yield hwnd, worker.engine.ui.snapshot()
        except Exception as exc:
            print("%s window %s: %s" % (label, hwnd, exc))


def claude():
    worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
    quota = QuotaLog()
    for hwnd, root in windows_of(worker, "Claude"):
        panes, entries = sa.discover(root)
        print("Claude window %s: %d open, %d in the sidebar (mode %s)"
              % (hwnd, len(panes), len(entries), sa.sidebar_mode(list(root.walk()))))
        for p in panes:
            state = sa.signals(p, app.detect_limit_banner)
            from_log = logged(quota, p.title)
            meter = sa.meter_node(p)
            print("  [%s] %s" % (p.kind, ascii_safe(p.title)))
            print("     %s" % flags(state))
            print("     question: %s   permission: %s   draft: %s"
                  % (question(state["question"]), "yes" if state["permission"] else "no", bool(sa.ClaudeUI.value(p.prompt))))
            print("     next step: %s   new session card: %s" % (next_step(state["next_step"]), new_session(state["offer"])))
            print("     context: window %s, log %s, combined %s" % (
                state["context"], from_log,
                sa.merged_context(state["context"], from_log if isinstance(from_log, dict) else None)))
            settings = sa.chat_settings(p.ui_nodes())
            print("     settings: %s" % ascii_safe(" / ".join(settings[k] or "-" for k in ("model", "effort", "mode"))))
            print("     meter: %r -> %s" % (ascii_safe(meter.name) if meter else None,
                                           sa.usage_meter(p, app.parse_reset_time)))
    try:
        print("Claude Code's log: newest rejected request resets %s" % quota.latest_reset())
    except Exception as exc:
        print("Claude Code's log: unreadable (%s)" % exc)


def chatgpt():
    worker = gpt.ChatGPTWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
    codex = CodexLog()
    for hwnd, root in windows_of(worker, "ChatGPT"):
        panes, entries = gpt_ui.discover(root)
        print("ChatGPT window %s: %d open, %d in the sidebar" % (hwnd, len(panes), len(entries)))
        for p in panes:
            state = gpt_ui.signals(p)
            print("  [%s] %s" % (p.kind, ascii_safe(p.title)))
            print("     %s  candidate=%s" % (flags(state), state["candidate"]))
            print("     context from Codex's log: %s   draft: %s" % (logged(codex, p.title), bool(sa.ClaudeUI.value(p.prompt))))
            settings = gpt_ui.gpt_settings(p)
            print("     settings: %s" % ascii_safe(" / ".join(settings[k] or "-" for k in ("model", "mode"))))


def main(argv):
    which = argv[1:] or ["claude", "chatgpt"]
    if any(w not in ("claude", "chatgpt") for w in which):
        raise SystemExit(__doc__)
    with app.auto.UIAutomationInitializerInThread():
        if "claude" in which:
            claude()
        if "chatgpt" in which:
            chatgpt()


if __name__ == "__main__":
    main(sys.argv)
