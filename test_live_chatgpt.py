"""Opt-in smoke checks against an open ChatGPT window. No messages are sent.

Run `py test_live_chatgpt.py` for discovery, `--usage` to open the profile menu and
read "Usage remaining" (the menu is closed again), `--composer-and-restore` to type
literal text into an idle empty composer and clear it without sending.
"""
import argparse
import queue

import chatgpt_auto_continue as gpt
from chatgpt_automation import composer_box, discover, signals, SEND


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usage", action="store_true")
    parser.add_argument("--composer-and-restore", action="store_true",
                        help="Exercise literal text in an idle empty composer, then clear it without sending")
    args = parser.parse_args()
    with gpt.auto.UIAutomationInitializerInThread():
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        windows = worker._enum_windows()
        if not windows:
            raise RuntimeError("This smoke test needs an open ChatGPT window")
        worker.hwnd = windows[0][0]
        ui = worker.engine.ui
        panes, entries = discover(ui.snapshot())
        print(f"Found {len(panes)} open conversation(s) and {len(entries)} sidebar chats")
        for p in panes:
            s = signals(p)
            print(f"Open {p.kind} chat: draft={bool(ui.value(p.prompt))} limit={s['limit']} "
                  f"reset={s['reset']} busy={s['busy']} error={bool(s['error'])}")
        if args.usage:
            rows, ok = worker._read_usage_menu(worker._get_window())
            assert ok, "Usage remaining could not be read"
            for r in rows:
                print(f"  {r['label']!r}: {r['left']}% left, resets {r['reset']}")
            menus = [n for n in ui.snapshot().walk() if n.type == "MenuControl" or n.role == "menu"]
            assert not menus, "The profile menu was left open"
            print("Profile menu closed again")
        if args.composer_and_restore:
            candidates = [p for p in panes if not signals(p)["busy"] and not ui.value(p.prompt)]
            assert candidates, "No idle empty composer"
            p = candidates[0]
            text = "Auto-Resume local input test: {braces} + ąćęłńóśźż"
            try:
                ui.type_into(p.prompt, text)
                print("Composer accepted literal text in the intended conversation")
                current = ui.resolve(p.key, navigate=False)
                box = composer_box(current.prompt)
                sends = [n for n in box.walk() if n.type == "ButtonControl" and n.name.strip().casefold() in SEND]
                assert any(n.control.IsEnabled for n in sends), "Input did not show an enabled Send"
                print("ChatGPT reacted to the input; Send is enabled")
            finally:
                current = ui.resolve(p.key, navigate=False)
                assert current, "Target conversation changed"
                ui.click(current.prompt)
                gpt.auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.2)
                current = ui.resolve(p.key, navigate=False)
                assert not ui.value(current.prompt), "Composer restore failed"
                print("Original empty composer restored; no message sent")


if __name__ == "__main__":
    main()
