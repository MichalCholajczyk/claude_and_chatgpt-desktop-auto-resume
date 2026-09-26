# -*- coding: utf-8 -*-
"""Window diagnostics for Auto-Resume development: what are the controls called?

The handover has to find a few controls the tool never touched before (the "new
conversation" button, the control naming the project) and the pause has to recognise
the "… is using your computer" overlay. This prints their real names, so the code can
match them instead of guessing. Conversation text is never printed.

    py -3 tools/probe_ui.py claude                 # controls outside the transcript
    py -3 tools/probe_ui.py claude "usage|context" # ...matching a pattern
    py -3 tools/probe_ui.py chatgpt "projekt|project"
    py -3 tools/probe_ui.py overlays               # top-level windows of both apps
    py -3 tools/probe_ui.py overlays --watch 25    # ...sampled for N seconds
    py -3 tools/probe_ui.py claude --open-new      # clicks "New", then prints the pane

Everything reads the window through UI Automation. `--open-new` is the one exception:
it clicks, because the new-conversation screen cannot be seen any other way.
"""
import ctypes
import ctypes.wintypes
import os
import queue
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_auto_continue as app                     # noqa: E402
import chatgpt_auto_continue as gpt                     # noqa: E402

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
APP_EXES = ("claude.exe", "chatgpt.exe", "codex.exe")
NEW_LABELS = ("new", "new session", "new chat", "nowa", "nowa sesja", "nowy czat")


def ascii_safe(text):
    return (text or "").encode("ascii", "replace").decode()


def worker_for(which):
    cls = app.MonitorWorker if which == "claude" else gpt.ChatGPTWorker
    worker = cls(queue.Queue(), dict(app.DEFAULT_CONFIG))
    worker.log = lambda *a, **kw: None
    windows = worker._enum_windows()
    if not windows:
        raise SystemExit("no %s window found" % which)
    worker.hwnd = windows[0][0]
    return worker, windows[0][1]


def dump(which, pattern=None):
    """Names of controls outside the sidebar and the transcript, in reading order."""
    worker, title = worker_for(which)
    regex = re.compile(pattern, re.I) if pattern else None
    root = worker.engine.ui.snapshot()
    print("WINDOW %s (%s)" % (worker.hwnd, ascii_safe(title)))
    for node in root.walk():
        if node.inside("chat messages") or not node.name.strip() or len(node.name) > 200:
            continue
        if regex and not regex.search(node.name):
            continue
        left, top, right, bottom = node.rect
        print("  %-18s %-60r w=%-5d y=%d" % (node.type, ascii_safe(node.name)[:60],
                                             right - left, top))


def open_new(which):
    """Click the "new conversation" button, then print the pane that appears."""
    worker, _ = worker_for(which)
    ui = worker.engine.ui
    root = ui.snapshot()
    before = {p.key for p in worker.engine.discover(root)[0]}
    buttons = [n for n in root.walk() if n.type == "ButtonControl"
               and n.name.strip().casefold() in NEW_LABELS and n.visible]
    print("new-conversation candidates:", [ascii_safe(b.name) for b in buttons])
    if len(buttons) != 1:
        raise SystemExit("cannot tell which button starts a new conversation")
    ui.click(buttons[0])
    time.sleep(1.5)
    root = ui.snapshot()
    panes, _ = worker.engine.discover(root)
    fresh = [p for p in panes if p.key not in before]
    print("panes before:", sorted(ascii_safe(k) for k in before))
    print("panes now:   ", sorted(ascii_safe(p.key) for p in panes))
    print("new pane:    ", [ascii_safe(p.key) for p in fresh] or "none (no pane key yet)")
    scope = fresh[0].root if fresh else root
    for node in scope.walk():
        if node.inside("chat messages") or not node.name.strip() or len(node.name) > 200:
            continue
        left, top, right, bottom = node.rect
        print("  %-18s %-70r w=%-5d y=%d" % (node.type, ascii_safe(node.name)[:70],
                                             right - left, top))


def exe_of(hwnd):
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
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


def top_windows():
    """[(hwnd, exe, title, (l, t, r, b))] for visible windows of Claude/ChatGPT/Codex."""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        exe = exe_of(hwnd)
        if exe not in APP_EXES:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(buf, length + 1)
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append((hwnd, exe, buf.value, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    user32.EnumWindows(visit, 0)
    return found


def shallow_text(hwnd, limit=40):
    """First control names inside a window — enough to spot an overlay's wording."""
    import uiautomation as auto
    try:
        control = auto.ControlFromHandle(hwnd)
    except Exception:
        return []
    names = []
    try:
        for ctrl, _depth in auto.WalkControl(control, includeTop=True, maxDepth=6):
            try:
                name = (ctrl.Name or "").strip()
            except Exception:
                continue
            if name:
                names.append(ascii_safe(name)[:60])
            if len(names) >= limit:
                break
    except Exception:
        pass
    return names


def overlays(watch_s=0):
    """Print every visible Claude/ChatGPT/Codex window; with watch_s, keep sampling."""
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        screen = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        print("screen:", screen)
        seen = set()
        deadline = time.monotonic() + watch_s
        while True:
            for hwnd, exe, title, rect in top_windows():
                width, height = rect[2] - rect[0], rect[3] - rect[1]
                fingerprint = (hwnd, title, width, height)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                small = width * height < 0.4 * screen[0] * screen[1]
                print("[%s] hwnd=%s exe=%-12s %dx%d at %s,%s small=%s title=%r"
                      % (time.strftime("%H:%M:%S"), hwnd, exe, width, height,
                         rect[0], rect[1], small, ascii_safe(title)))
                if small:
                    print("      text:", shallow_text(hwnd))
            if time.monotonic() >= deadline:
                return
            time.sleep(0.5)


def main(argv):
    mode = argv[1] if len(argv) > 1 else "claude"
    rest = argv[2:]
    if mode == "overlays":
        watch = 0
        if "--watch" in rest:
            index = rest.index("--watch")
            watch = float(rest[index + 1]) if len(rest) > index + 1 else 20
        overlays(watch)
        return
    if mode not in ("claude", "chatgpt"):
        raise SystemExit(__doc__)
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        if "--open-new" in rest:
            open_new(mode)
        else:
            dump(mode, rest[0] if rest and not rest[0].startswith("--") else None)


if __name__ == "__main__":
    main(sys.argv)
