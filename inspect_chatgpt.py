"""Read-only check of what ChatGPT Auto-Resume sees in the ChatGPT window.

py inspect_chatgpt.py            conversations, composer state and limit signals (nothing is sent anywhere)
py inspect_chatgpt.py --usage    also open the profile menu -> "Usage remaining" and print the rows
py inspect_chatgpt.py --dump     also write the window's accessibility tree to chatgpt_tree.txt
                                 (it contains the conversation's text: share it only if you want to)
"""
import json
import os
import queue
import sys

import chatgpt_auto_continue as gpt
from chatgpt_automation import composer_box, discover, document_title, last_turn, signals


def why_no_conversation(root):
    """What discover() needs to see an open conversation, as found in this window."""
    nodes = list(root.walk())
    main = [n for n in nodes if n.role == "main"]
    title = document_title(nodes)
    return dict(main_landmarks=len(main),
                visible_documents=[n.name for n in nodes if n.type == "DocumentControl" and n.visible],
                title_buttons=sum(1 for n in nodes if n.type == "ButtonControl" and title and n.name.strip() == title),
                composers=sum(1 for n in nodes if n.type == "EditControl" and n.visible and composer_box(n)))


def dump(root, path):
    lines = []

    def visit(n, depth):
        lines.append(f"{'  ' * depth}{n.type} role={n.role!r} rect={n.rect} name={n.name[:300]!r}")
        for c in n.children:
            visit(c, depth + 1)
    visit(root, 0)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    with gpt.auto.UIAutomationInitializerInThread():
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        windows = worker._enum_windows()
        result = []
        for hwnd, title in windows:
            worker.hwnd = hwnd
            ui = worker.engine.ui
            try:
                root = ui.snapshot()
                panes, entries = discover(root)
            except Exception as exc:
                result.append(dict(hwnd=hwnd, title=title, error=str(exc)))
                continue
            info = dict(hwnd=hwnd, title=title, sidebar=[e["key"] for e in entries], panes=[])
            for p in panes:
                s = signals(p)
                _, words, _, _ = last_turn(p)
                info["panes"].append(dict(key=p.key, draft=bool(ui.value(p.prompt)), limit=s["limit"],
                                          candidate=s["candidate"], reset=str(s["reset"]), busy=s["busy"],
                                          error=s["error"], retry=bool(s["retry"]),
                                          last_texts=[w.name.strip()[:100] for w in words[-5:]]))
            if not panes:
                info["why_no_conversation"] = why_no_conversation(root)
            if "--dump" in sys.argv:
                path = os.path.join(gpt.app.APP_DIR, "chatgpt_tree.txt" if len(windows) == 1 else f"chatgpt_tree_{hwnd}.txt")
                dump(root, path)
                info["dump"] = path
            if "--usage" in sys.argv:
                rows, ok = worker._read_usage_menu(worker._get_window())
                info["usage"] = dict(ok=ok, rows=[{k: str(v) for k, v in r.items()} for r in rows])
            result.append(info)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
