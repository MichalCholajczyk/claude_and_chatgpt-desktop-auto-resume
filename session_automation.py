"""Scoped Claude accessibility adapter and independent per-conversation scheduler.

No coordinates or UIA objects are persisted. Every action resolves its conversation
again; ambiguous titles, drafts and unknown controls fail closed.
"""
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import os
import re
import time

from handover import BLOCKED_PHASES, HandoverFlow, HandoverRefused, context_from_meter
from input_guard import InputPaused
from input_lock import input_lock, worker_cancelled
from quota_log import QuotaLog


PROMPTS = {"prompt", "write your prompt to claude", "reply to claude", "message claude"}
RETRY = {"try again", "retry", "spróbuj ponownie", "ponów"}
SUBMIT = {"submit", "wyślij", "zatwierdź"}
# The card's main button reads "Next" on every question but the last one.
PRIMARY = SUBMIT | {"next", "dalej"}
QUESTION_CONTROLS = PRIMARY | {"dismiss question", "view question options", "skip", "back",
                               "next question", "previous question"}
OTHER = {"other", "inne", "inna", "inny"}
PICK_FOR_ME = "Pick your recommended option(s)."
RECOMMENDED = re.compile(r"\b(?:recommended|rekomendowan\w*|rekomendacja|zalecan\w*|polecan\w*)\b", re.I)
NEGATIVE = re.compile(r"\b(?:not\s+recommended|nie\s+(?:zalecan\w*|polecan\w*|rekomendowan\w*))\b", re.I)
API_ERROR = re.compile(
    r"^(?:API\s*Error\b|API\s*error:|Error:\s*(?:5\d\d|429)\b|"
    r"overloaded_error\b|internal_server_error\b|"
    r"(?:the\s+)?server\s+is\s+(?:overloaded|busy)|"
    r"Claude is (?:currently )?(?:overloaded|experiencing)|"
    r"Something went wrong|An error occurred|Network error|"
    r"Connection error|Request timed out|Serwer jest przeciążony|Błąd API)", re.I)
PERMANENT_ERROR = re.compile(r"\b(?:400|401|402|403|404|413|authentication_error|permission_error|billing_error|invalid_request_error)\b", re.I)
# Headline of Claude Code's limit card. Spend and credit cards are excluded:
# they do not clear on a usage reset, so resuming there cannot help.
LIMIT_HEADLINE = re.compile(r"(?:usage|session|weekly|(?:claude\s+)?(?:opus|sonnet|haiku|fable)"
                            r"(?:\s[\d.]+)?)\s+limit\s+reached", re.I)
CARD_ACTIONS = {"try again", "view details", "hide details"}
STOP_LABELS = {"stop", "stop response", "stop generating", "zatrzymaj"}
# The sidebar button that starts a conversation, and how long its title may take.
NEW_CHAT_LABELS = {"new", "new session", "new chat", "nowa", "nowa sesja", "nowy czat"}
SHOW_SIDEBAR_LABELS = {"show sidebar", "pokaż pasek boczny", "pokaż pasek boczny"}
NEW_TITLE_WAIT_S = 40
# Buttons of the permission card ("Allow Claude to fetch …?") end with their key digit,
# e.g. "Allow once 2" (captured 25.09); key names are stripped too, should a version
# spell them out. The allow buttons in order of preference.
KEY_HINTS = re.compile(r"(?:\s*(?:\d+|ctrl|shift|alt|esc|enter|return|[⏎↵⌘⇧⌃⌥+]))+\s*$", re.I)
ALLOW = ("always allow", "allow once")
USAGE_METER = re.compile(r"usage\s*[:,]", re.I)
# The context part of the meter ("Context 195.8k / 1M (20%)", or just "context 20%" in
# older builds) is not a plan limit: its own percentage must never be read as a used-up
# plan window, so the whole part is cut out before the percentages are collected.
METER_CONTEXT = re.compile(r"context\s*:?\s*\d{1,3}\s*%|"
                           r"context\s*:?\s*[\d.,]+\s*[km]?"
                           r"(?:\s*/\s*[\d.,]+\s*[km]?)?(?:\s*\(\s*\d{1,3}\s*%\s*\))?", re.I)


class WindowHidden(RuntimeError):
    """The app window is fully covered or minimized. Chromium then hides the page and
    exposes an empty document, so nothing in it can be read until it is visible."""


def key_for(kind, title):
    return kind + ":" + title.strip()


def content_hidden(root):
    """True when every web document in the window is empty and has no size."""
    documents = [n for n in root.walk() if n.type == "DocumentControl"]
    return bool(documents) and all(not n.visible and not n.children for n in documents)


@dataclass(eq=False)
class Node:
    name: str
    type: str
    rect: tuple = (0, 0, 0, 0)
    control: object = None
    parent: object = None
    children: list = field(default_factory=list)
    role: str = ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node:
            yield node
            node = node.parent

    def inside(self, name):
        return any(n.name.casefold() == name for n in self.ancestors())

    @property
    def visible(self):
        l, t, r, b = self.rect
        return r > l and b > t


@dataclass
class Pane:
    key: str
    title: str
    kind: str
    root: Node
    prompt: Node

    def ui_nodes(self):
        return [n for n in self.root.walk() if not n.inside("chat messages")]


def rename_title(node):
    if node.type != "ButtonControl":
        return None
    match = re.fullmatch(r"(.+), rename (session|chat|task)", node.name)
    return (match[1], "code" if match[2] == "session" else "chat") if match else None


def discover(root):
    """Return named panes and sidebar entries. Duplicate keys remain ambiguous."""
    nodes = list(root.walk())
    prompts = [n for n in nodes if n.type == "EditControl" and
               n.name.casefold() in PROMPTS and n.visible]
    panes = []
    for prompt in prompts:
        for ancestor in prompt.ancestors():
            descendants = list(ancestor.walk())
            if sum(p in descendants for p in prompts) != 1:
                break
            titles = [rename_title(n) for n in descendants if rename_title(n)]
            if len(titles) == 1:
                title, kind = titles[0]
                panes.append(Pane(key_for(kind, title), title, kind, ancestor, prompt))
                break
    sidebar = [n for n in nodes if n.name.casefold() == "sidebar"]
    entries = []
    for bar in sidebar:
        bar_nodes = list(bar.walk())
        kind = "chat" if any(n.name == "Chats and tasks" for n in bar_nodes) else "code"
        # Claude exposes the full title in the companion menu button, even when
        # the displayed text is truncated. The row itself is a different button.
        for menu in bar_nodes:
            if menu.type != "ButtonControl" or not menu.name.startswith("More options for "):
                continue
            title = menu.name[len("More options for "):]
            row = None
            for ancestor in menu.ancestors():
                if ancestor is bar:
                    break
                matches = [n for n in ancestor.walk() if n is not menu and
                           n.type in ("ButtonControl", "HyperlinkControl") and
                           (n.name == title or n.name.endswith(" " + title)) and
                           not n.name.startswith("More options for ")]
                if matches:
                    row = matches[0] if len(matches) == 1 else None
                    break
            if row:
                entries.append(dict(key=key_for(kind, title), title=title,
                                    kind=kind, node=row, source="sidebar"))
    return panes, entries


def question_in(pane):
    """Only the live question widget; never infer choices from chat prose."""
    nodes = pane.ui_nodes()
    for marker in nodes:
        if marker.type != "ButtonControl" or marker.name != "Dismiss question":
            continue
        group = marker.parent
        children = list(group.walk())
        submit = [n for n in children if n.type == "ButtonControl" and n.name.casefold() in PRIMARY]
        if len(submit) != 1:
            continue
        options = [n for n in group.children if n.type in
                   ("ButtonControl", "CheckBoxControl", "RadioButtonControl") and
                   n.name.casefold() not in QUESTION_CONTROLS]
        if len(options) < 2:
            continue
        # The first text child is the label; descriptions may mention other
        # recommendations and must not select a different option by accident.
        labels = [next((n.name for n in option.walk() if n.type == "TextControl"), option.name).strip()
                  for option in options]
        recommended = [option for option, label in zip(options, labels)
                       if RECOMMENDED.search(label) and not NEGATIVE.search(label)]
        other = [n for n in children if n.type == "EditControl" and
                 n.name.casefold() in {"other option", "other", "inna odpowiedź"}]
        # Identity from text that does not change while options are ticked:
        # the question (and "2/3" counter), the option labels and the main button.
        in_options = {n for option in options for n in option.walk()}
        texts = [n.name for n in children if n.type == "TextControl" and n not in in_options]
        identity = "\n".join([*texts, *labels, submit[0].name])
        return dict(group=group, options=options, labels=labels, recommended=recommended,
                    other=other[0] if len(other) == 1 else None,
                    submit=submit[0], fingerprint=hashlib.sha256(identity.encode()).hexdigest())
    return None


def allow_name(name):
    """True for a name like the permission card's allow buttons ("Allow once 2")."""
    return KEY_HINTS.sub("", name).strip().casefold() in ALLOW


def button_label(node):
    """A button's own label: its first text, else its name without the key that
    Claude's cards append ("Allow once 2" -> "Allow once")."""
    text = next((n.name for n in node.walk() if n.type == "TextControl" and n.name.strip()), None)
    return (text if text is not None else KEY_HINTS.sub("", node.name)).strip()


def permission_in(pane):
    """The live permission card: Deny plus "Always allow" and/or "Allow once", outside
    the transcript. A question card never counts, even when its answers read like
    permission buttons."""
    composer_line = set(pane.prompt.ancestors())
    for deny in pane.ui_nodes():
        if deny.type != "ButtonControl" or button_label(deny).casefold() != "deny":
            continue
        # The card is the widest ancestor that does not also hold the composer.
        card = deny
        for ancestor in deny.ancestors():
            if ancestor in composer_line:
                break
            card = ancestor
        buttons = [n for n in card.walk() if n.type == "ButtonControl"]
        if any(n.name == "Dismiss question" for n in buttons):
            continue
        labels = [button_label(n).casefold() for n in buttons]
        found = {wanted: [b for b, label in zip(buttons, labels) if label == wanted]
                 for wanted in ("deny", *ALLOW)}
        if (len(found["deny"]) != 1 or not any(found[a] for a in ALLOW)
                or any(len(found[a]) > 1 for a in ALLOW)):
            continue
        in_buttons = {n for b in buttons for n in b.walk()}
        texts = [n.name.strip() for n in card.walk()
                 if n.type == "TextControl" and n.name.strip() and n not in in_buttons]
        # The question opens the card as a run of texts: "Allow Claude to ", "run", " ", what, "?".
        lead = []
        for child in card.children:
            if child.type != "TextControl":
                break
            lead.append(child.name)
        identity = "\n".join([*texts, *labels])
        return dict(card=card, always=next(iter(found["always allow"]), None),
                    once=next(iter(found["allow once"]), None),
                    title=" ".join("".join(lead).split()) or next(iter(texts), ""),
                    fingerprint=hashlib.sha256(identity.encode()).hexdigest())
    return None


def final_limit_card(pane, parse):
    """Claude Code ends a blocked conversation with a card in the transcript:
    "Session limit reached" / "Try again after your session limit resets." /
    View details / Try again (older cards collapse to a "Session limit reached"
    button). Only the last message counts; earlier cards stay after a resume.
    Returns (found, reset_or_None)."""
    articles = [n for n in pane.root.walk() if n.role == "article" and n.inside("chat messages")]
    if not articles:
        return False, None
    nodes = list(articles[-1].walk())
    if any(n.name.startswith("You said:") for n in nodes):
        return False, None
    headlines = [n for n in nodes if LIMIT_HEADLINE.fullmatch(n.name.strip())]
    collapsed = any(n.type == "ButtonControl" for n in headlines)
    actions = any(n.type == "ButtonControl" and n.name.strip().casefold() in CARD_ACTIONS for n in nodes)
    if not headlines or not (collapsed or actions):
        return False, None
    resets = (parse(n.name) for n in nodes if n.type == "TextControl" and "reset" in n.name.casefold())
    return True, next((r for r in resets if r), None)


def meter_node(pane):
    """The bottom-bar usage meter of this conversation, or None."""
    return next((n for n in pane.ui_nodes() if n.type == "ButtonControl"
                 and USAGE_METER.match(n.name)), None)


def usage_meter(pane, parse):
    """Highest plan percentage, reset time and context reading from the bottom-bar
    meter, e.g. "Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit, Resets at
    9:30 AM" or "Usage, Weekly · all models: 19%, Resets Mon 6:00 PM"."""
    meter = meter_node(pane)
    if meter is None:
        return dict(pct=None, reset=None, context=None)
    percentages = [int(p) for p in re.findall(r"(\d{1,3})\s*%", METER_CONTEXT.sub("", meter.name))]
    return dict(pct=max(percentages) if percentages else None, reset=parse(meter.name),
                context=context_from_meter(meter.name))


def last_reply_text(pane):
    """All text of the conversation's last message, when that message is Claude's.

    The handover marker is looked for here. Our own request quotes the marker's shape,
    so a message of ours must never count as an answer.
    """
    articles = [n for n in pane.root.walk() if n.role == "article" and n.inside("chat messages")]
    if not articles:
        return ""
    nodes = list(articles[-1].walk())
    if any(n.name.startswith("You said:") for n in nodes):
        return ""
    return " ".join(n.name.strip() for n in nodes if n.name.strip())


def choose_reset(candidates, now):
    """When a limit can clear: the latest future reset among the sources, else
    the most recent past one (the limit already reset), else None."""
    known = [c for c in candidates if c is not None]
    future = [c for c in known if c > now]
    return max(future) if future else max(known, default=None)


def signals(pane, detect_banner):
    from claude_auto_continue import parse_reset_time
    question = question_in(pane)
    permission = permission_in(pane)
    card_nodes = set(question["group"].walk()) if question else set()
    if permission:
        card_nodes.update(permission["card"].walk())
    ui = [n for n in pane.ui_nodes() if n not in card_nodes and not rename_title(n)
          and not n.name.startswith("More options for ") and n.type != "EditControl"
          and not any(a.type == "EditControl" or a.name == "Repository and pull request controls"
                      for a in n.ancestors())]
    texts = [n.name for n in ui if n.type in ("TextControl", "ButtonControl", "StatusBarControl")]
    banner, reset = detect_banner(texts)
    retries = [n for n in ui if n.type == "ButtonControl" and n.name.casefold() in RETRY and n.visible]
    errors = [n.name for n in ui if n.type in ("TextControl", "StatusBarControl") and API_ERROR.search(n.name)]
    # Code can render a terminal API failure at the end of the transcript.
    # Consider only the last text leaf, not old errors, quotes, code blocks or
    # user messages. An arbitrary occurrence of the phrase is never sufficient.
    chat_nodes = [n for n in pane.root.walk() if n.inside("chat messages")]
    leaves = [n for n in chat_nodes if n.type == "TextControl" and n.name.strip()
              and not any(a.name == "Message actions" for a in n.ancestors())]
    if leaves:
        last = leaves[-1]
        ancestors = list(last.ancestors())
        if (API_ERROR.search(last.name) and not any(a.type in ("CodeControl", "EditControl") or
                a.role in ("code", "blockquote") or
                a.name.startswith(("You said:", "Message ")) for a in ancestors)):
            errors.append(last.name)
    error = errors[-1] if errors else None
    card, card_reset = final_limit_card(pane, parse_reset_time)
    meter = meter_node(pane)
    return dict(context=context_from_meter(meter.name) if meter is not None else None,
                last_text=last_reply_text(pane),
                limit=bool(banner) or card, reset=reset or card_reset, error=error,
                permanent=bool(error and PERMANENT_ERROR.search(error)),
                retry=retries[0] if len(retries) == 1 else None,
                busy=any(n.type == "ButtonControl" and n.name.casefold() in
                         {"stop", "stop response", "stop generating", "zatrzymaj"} for n in ui),
                question=question, permission=permission)


class ClaudeUI:
    def __init__(self, worker):
        self.worker = worker
        self.clicks = 0     # clicks attempted; tells whether a failed action had any effect

    def snapshot(self):
        from claude_auto_continue import auto
        win = self.worker._get_window()
        if win is None:
            raise RuntimeError("Claude window is unavailable")
        self.worker._wake_accessibility(win)
        root = Node("window", "WindowControl", control=win)
        stack = [(-1, root)]
        started = time.monotonic()
        for i, (ctrl, depth) in enumerate(auto.WalkControl(win, includeTop=False, maxDepth=130)):
            if i > 30000 or time.monotonic() - started > 15:
                raise RuntimeError("Accessibility scan incomplete; no action taken")
            try:
                r = ctrl.BoundingRectangle
                node = Node(ctrl.Name or "", ctrl.ControlTypeName,
                            (r.left, r.top, r.right, r.bottom), ctrl, role=ctrl.AriaRole or "")
            except Exception:
                raise RuntimeError("Accessibility tree changed; retry on next scan")
            while stack[-1][0] >= depth:
                stack.pop()
            node.parent = stack[-1][1]
            node.parent.children.append(node)
            stack.append((depth, node))
        if content_hidden(root):
            raise WindowHidden("The app window is covered or minimized")
        return root

    def click(self, node):
        from claude_auto_continue import user32
        if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
            raise RuntimeError("Pending command; action deferred")
        # Checked again here, not only once per scan: someone may have grabbed the
        # mouse in the middle of a read-click-type sequence.
        if self.worker.guard.user_busy():
            raise InputPaused("Someone is using the mouse; action deferred")
        self.worker._focus_window(self.worker.hwnd)
        if user32.GetForegroundWindow() != self.worker.hwnd:
            raise RuntimeError("Claude is not foreground")
        if not node.visible or not node.control.IsEnabled or node.control.IsOffscreen:
            raise RuntimeError("Control is unavailable")
        self.clicks += 1
        with self.worker.guard.sending():
            node.control.Click(simulateMove=False)

    def press(self, keys, wait=0.1):
        """Send keys to whatever has the focus, and record them as our own input."""
        from claude_auto_continue import auto
        if self.worker.guard.user_busy():
            raise InputPaused("Someone is using the mouse; keys were not sent")
        with self.worker.guard.sending():
            auto.SendKeys(keys, waitTime=wait)

    def resolve(self, key, navigate=True):
        root = self.snapshot()
        panes, entries = discover(root)
        matches = [p for p in panes if p.key == key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 or not navigate:
            return None
        kind = key.split(":", 1)[0]
        rows = [e for e in entries if e["key"] == key]
        if not rows:
            label = "Code" if kind == "code" else "Chat and Cowork"
            switches = [n for n in root.walk() if n.type == "ButtonControl" and
                        (n.name == label or n.name.startswith(label + ",")) and n.inside("sidebar")]
            if len(switches) == 1:
                self.click(switches[0])
                time.sleep(0.8)
                root = self.snapshot()
                panes, entries = discover(root)
                rows = [e for e in entries if e["key"] == key]
        if len(rows) != 1:
            return None
        self.click(rows[0]["node"])
        # Navigation is asynchronous. Verify the requested full title and its
        # own composer, never assume that a successful click opened the chat.
        for _ in range(4):
            time.sleep(0.4)
            panes, _ = discover(self.snapshot())
            matches = [p for p in panes if p.key == key]
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def value(node):
        try:
            value = node.control.GetValuePattern().Value.strip()
        except Exception:
            try:
                value = node.control.GetTextPattern().DocumentRange.GetText(-1).strip()
            except Exception:
                raise RuntimeError("Cannot verify composer contents")
        # Code includes its non-editable placeholder in both UIA text patterns.
        # It has a nested label plus a separate empty editable text leaf. A typed
        # draft with the same words has no separate placeholder group.
        leaves = [n for n in node.walk() if n.type == "TextControl"]
        if (node.name.casefold() == "prompt" and value in {"Type / for commands", "Type a message..."}
                and len(leaves) == 2 and not leaves[-1].name.strip()
                and leaves[0].parent is not node):
            return ""
        return value

    def type_into(self, node, text):
        from claude_auto_continue import auto, user32
        if self.value(node):
            raise RuntimeError("Existing draft; leaving it untouched")
        self.click(node)
        time.sleep(0.15)
        if user32.GetForegroundWindow() != self.worker.hwnd or not node.control.HasKeyboardFocus:
            raise RuntimeError("Composer focus could not be verified")
        # Prefer an element-scoped write; Chromium sometimes exposes a read-only
        # ValuePattern. Fallback chunks recheck focus and pending Stop commands.
        wrote_value = False
        try:
            pattern = node.control.GetValuePattern()
            if pattern and not pattern.IsReadOnly:
                pattern.SetValue(text)
                wrote_value = True
                self.worker.guard.mark_own_input()
        except Exception:
            if self.value(node):
                raise RuntimeError("Text write outcome is uncertain; leaving the draft untouched")
        if not wrote_value:
            for start in range(0, len(text), 32):
                if (user32.GetForegroundWindow() != self.worker.hwnd or not node.control.HasKeyboardFocus
                        or self.worker._stop_event.is_set() or not self.worker.cmds.empty()):
                    raise RuntimeError("Input interrupted; message was not submitted")
                if self.worker.guard.user_busy():
                    raise InputPaused("Someone took the mouse; message was not submitted")
                with self.worker.guard.sending():
                    auto.SendKeys(self.worker._escape_sendkeys(text[start:start + 32]), interval=0.001, waitTime=0.01)
        if user32.GetForegroundWindow() != self.worker.hwnd or not node.control.HasKeyboardFocus:
            raise RuntimeError("Focus changed; message was not submitted")
        if self.value(node) != text:
            raise RuntimeError("Typed text could not be verified; message was not submitted")

    def send(self, pane, message):
        """Type one message into this conversation and submit it. Returns what was sent.

        Enter submits in the composer, so the text goes in as a single line: line breaks
        would send it half-written.
        """
        text = " ".join((message or "").split())
        if not text:
            raise RuntimeError("Nothing to send")
        self.type_into(pane.prompt, text)
        if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
            raise RuntimeError("Pending command; message was not submitted")
        self.press("{Enter}")
        return text

    def stop(self, pane):
        """Interrupt the agent's work in this conversation. True when Stop was clicked."""
        buttons = [n for n in pane.ui_nodes() if n.type == "ButtonControl"
                   and n.name.strip().casefold() in STOP_LABELS and n.visible]
        if len(buttons) != 1:
            return False
        self.click(buttons[0])
        return True

    def resume(self, key, prefer_retry=False, api_error=False, message=None):
        """Continue a conversation that stopped. `message` overrides the user's text (the
        handover asks for its own message); then the retry button is never used, because
        it would repeat the interrupted request instead of asking anything."""
        from claude_auto_continue import DEFAULT_MESSAGE, detect_limit_banner
        pane = self.resolve(key)
        if pane is None:
            raise RuntimeError("Selected conversation is missing or ambiguous")
        state = signals(pane, detect_limit_banner)
        if state["question"] or state["permission"] or state["busy"]:
            return "busy"
        if self.value(pane.prompt):
            raise RuntimeError("Existing draft; leaving it untouched")
        if not message:
            if state["retry"] and (prefer_retry or api_error):
                self.click(state["retry"])
                return "retry"
            if api_error and not state["error"]:
                return "cleared"
        self.send(pane, message or self.worker.cfg.get("message") or DEFAULT_MESSAGE)
        return "message"

    # ------------------------------------------------- starting a conversation
    def panes(self, root=None):
        """Conversation panes of this app's window (ChatGPT's adapter reads its own)."""
        return discover(root if root is not None else self.snapshot())[0]

    def new_session_screen(self, root):
        """(prompt, project button) of the screen a new conversation starts on.

        It has no title yet, so `discover` does not see it as a pane. Measured layout
        (26.09): the group that holds the composer also holds, in this order, where to
        run it, the project, the branch box, "worktree" and "Add another folder" — so the
        project is the last button before the branch box.
        """
        taken = {pane.prompt for pane in self.panes(root)}
        prompts = [n for n in root.walk() if n.type == "EditControl" and n.visible
                   and n.name.casefold() in PROMPTS and n not in taken]
        if len(prompts) != 1:
            return None, None
        prompt = prompts[0]
        node = prompt.parent
        for _ in range(8):
            if node is None:
                break
            combos = [i for i, child in enumerate(node.children) if child.type == "ComboBoxControl"]
            if combos:
                buttons = [child for child in node.children[:combos[0]]
                           if child.type == "ButtonControl" and child.visible]
                return prompt, (buttons[-1] if buttons else None)
            node = node.parent
        return prompt, None

    @staticmethod
    def new_chat_buttons(root):
        return [n for n in root.walk() if n.type == "ButtonControl" and n.visible
                and n.name.strip().casefold() in NEW_CHAT_LABELS and n.inside("sidebar")]

    def show_sidebar(self, root):
        """Unfold the sidebar when the window is too narrow to keep it open. Returns the
        window as it is afterwards (unchanged when there is nothing to unfold)."""
        toggles = [n for n in root.walk() if n.type == "ButtonControl" and n.visible
                   and n.name.strip().casefold() in SHOW_SIDEBAR_LABELS]
        if len(toggles) != 1:
            return root
        self.click(toggles[0])
        time.sleep(0.6)
        return self.snapshot()

    def pick_project(self, button, wanted):
        """Choose the project by folder name from the button's own menu. The menu lists
        folder names, so it is compared with the folder the handover was written in."""
        self.click(button)
        for _ in range(6):
            time.sleep(0.4)
            root = self.snapshot()
            menus = [n for n in root.walk() if n.type == "MenuControl" or n.role == "menu"]
            items = [n for menu in menus for n in menu.walk()
                     if n.type in ("RadioButtonControl", "MenuItemControl", "ListItemControl")
                     and n.name.strip().casefold() == wanted and n.visible]
            if len(items) == 1:
                self.click(items[0])
                time.sleep(0.8)
                return True
            if menus and items:
                break                 # more than one match: never guess
        self.press("{Esc}")           # never leave a menu open behind us
        return False

    def new_chat(self, key, project_root, message):
        """Start a conversation in the same project as `key` and send it `message`.

        Returns the new conversation's key, or "" when its title has not settled yet.
        Raises HandoverRefused when the window cannot confirm the project or the screen:
        a message sent into the wrong project would be worse than no message at all.
        """
        wanted = os.path.basename(os.path.normpath(project_root or "")).casefold()
        if not wanted:
            raise HandoverRefused("The handover names no project folder")
        pane = self.resolve(key)
        if pane is None:
            raise HandoverRefused("The conversation the handover came from is missing")
        if self.value(pane.prompt):
            raise RuntimeError("Existing draft; leaving it untouched")
        self.click(pane.prompt)       # this pane's turn: the new conversation opens here
        root = self.snapshot()
        before = {p.key for p in self.panes(root)}
        buttons = self.new_chat_buttons(root)
        if len(buttons) != 1:
            # A narrow window keeps the sidebar folded away, and with it the button that
            # starts a conversation. Unfold it and look again.
            root = self.show_sidebar(root)
            buttons = self.new_chat_buttons(root)
        if len(buttons) != 1:
            raise HandoverRefused("Cannot tell which button starts a new conversation")
        self.click(buttons[0])
        prompt, project = None, None
        for _ in range(6):
            time.sleep(0.5)
            prompt, project = self.new_session_screen(self.snapshot())
            if prompt is not None and project is not None:
                break
        if prompt is None or project is None:
            raise HandoverRefused("The new conversation's screen could not be read")
        if project.name.strip().casefold() != wanted:
            if not self.pick_project(project, wanted):
                raise HandoverRefused("The project of the handover is not on the list")
            prompt, project = self.new_session_screen(self.snapshot())
            if prompt is None or project is None or project.name.strip().casefold() != wanted:
                raise HandoverRefused("The new conversation is not in the project of the handover")
        self.send(Pane("", "", "", root, prompt), message)
        return self.settled_key(before)

    def settled_key(self, before, wait_s=NEW_TITLE_WAIT_S):
        """The key of the conversation that appeared, once the app has named it."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            time.sleep(1.0)
            if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
                return ""
            try:
                panes = self.panes()
            except Exception:
                continue
            fresh = [p.key for p in panes if p.key not in before]
            if len(fresh) == 1:
                return fresh[0]
        return ""

    def answer(self, key, fingerprint):
        """Answer the live question card with its recommended choice, or ask
        Claude to choose via Other.

        Claude has two kinds of card. Single choice: clicking an option records
        the answer at once and moves to the next question (or submits the last
        one), so no selection state is exposed. Multiple choice: options toggle
        (aria-pressed) until Next / Submit. Answers already started are left alone."""
        pane = self.resolve(key)
        q = question_in(pane) if pane else None
        if not q or q["fingerprint"] != fingerprint:
            return False
        if q["other"] and self.value(q["other"]):
            raise RuntimeError("Question already has a draft answer")
        kinds = {self.selection_kind(n) for n in q["options"]}
        if len(kinds) != 1:
            raise RuntimeError("Cannot verify question selection state")
        kind = kinds.pop()   # "toggle": multiple choice; "select" or None: single choice
        # The main button stays disabled until something is chosen.
        if any(self.selected(n) for n in q["options"]) or self.enabled(q["submit"]):
            raise RuntimeError("Question already has a selected answer")
        chosen = [label for option, label in zip(q["options"], q["labels"]) if option in q["recommended"]]
        if kind != "toggle" and len(chosen) != 1:
            chosen = []   # a single pick: let Claude choose rather than guess between recommendations
        if not chosen:
            return self._pick_in_other(key, fingerprint, q)
        if kind is None:
            self.click(next(o for o, label in zip(q["options"], q["labels"]) if label == chosen[0]))
            return self._accepted(key, fingerprint)
        # Re-resolve between every selection: no stale coordinates when the
        # card grows, changes question, or the user rearranges a split.
        for label in chosen:
            current = self._same_question(key, fingerprint)
            option = current and next((o for o, l in zip(current["options"], current["labels"]) if l == label), None)
            if not option:
                return False
            self.click(option)
        current = self._same_question(key, fingerprint)
        if not current:
            return False
        picked = [o for o, label in zip(current["options"], current["labels"]) if label in chosen]
        if len(picked) != len(chosen) or not all(self.selected(o) for o in picked):
            raise RuntimeError("Recommended selections could not be verified")
        self.click(current["submit"])
        return True

    def _pick_in_other(self, key, fingerprint, q):
        """No single recommendation: ask Claude to choose, through the Other field."""
        if not q["other"]:
            raise RuntimeError("No recommended option or Other field")
        other = [o for o, label in zip(q["options"], q["labels"]) if label.casefold() in OTHER]
        if len(other) == 1:
            self.click(other[0])   # selects Other; even on a single-choice card this does not answer yet
            q = self._same_question(key, fingerprint)
            if not q:
                return False
        self.type_into(q["other"], PICK_FOR_ME)
        current = self._same_question(key, fingerprint)
        if not current:
            return False
        self.click(current["submit"])
        return True

    def _same_question(self, key, fingerprint):
        pane = self.resolve(key, navigate=False)
        q = question_in(pane) if pane else None
        return q if q and q["fingerprint"] == fingerprint else None

    def _accepted(self, key, fingerprint):
        """A single-choice click answers at once, so the card has to move on."""
        for _ in range(6):
            time.sleep(0.3)
            if self._same_question(key, fingerprint) is None:
                return True
        raise RuntimeError("Answer click was not accepted")

    def approve(self, key, card):
        """Allow a live permission card: "Always allow" when it is offered, else "Allow
        once"; never "Deny". Invoke first — it needs neither the focus nor the mouse —
        and a real click only when Claude ignored it. Returns "always" or "once"."""
        choice = "always" if card["always"] else "once"
        if self.invoke(card[choice]) and self._permission_gone(key, card["fingerprint"]):
            return choice
        current = self._same_permission(key, card["fingerprint"])
        if current is None:
            return choice
        self.click(current[choice])
        if self._permission_gone(key, card["fingerprint"]):
            return choice
        raise RuntimeError("Permission click was not accepted")

    def invoke(self, node):
        """Press a button through UI Automation, without focus change or mouse movement.
        False when the button offers no Invoke."""
        if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
            raise RuntimeError("Pending command; action deferred")
        if not node.visible or not node.control.IsEnabled or node.control.IsOffscreen:
            raise RuntimeError("Control is unavailable")
        pattern = node.control.GetInvokePattern()
        if pattern is None:
            return False
        self.clicks += 1
        pattern.Invoke()
        return True

    def permission_waiting(self):
        """Cheap check between scans: a native search (~30 ms) for a button named like
        the permission card's allow buttons. Only a hit is worth reading the window."""
        from claude_auto_continue import auto
        import uiautomation.uiautomation as core
        win = self.worker._get_window()
        if win is None:
            return False
        uia = core._AutomationClient.instance().IUIAutomation
        condition = uia.CreateAndCondition(
            uia.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, auto.ControlType.ButtonControl),
            uia.CreatePropertyConditionEx(auto.PropertyId.NameProperty, "allow", 3))   # any case, substring
        found = win.Element.FindAll(4, condition)                                    # all descendants
        return any(allow_name(found.GetElement(i).CurrentName or "") for i in range(found.Length))

    def _same_permission(self, key, fingerprint):
        pane = self.resolve(key, navigate=False)
        card = permission_in(pane) if pane else None
        return card if card and card["fingerprint"] == fingerprint else None

    def _permission_gone(self, key, fingerprint):
        for _ in range(3):
            time.sleep(0.4)
            if self._same_permission(key, fingerprint) is None:
                return True
        return False

    @staticmethod
    def selection_kind(node):
        """"toggle" (multiple choice), "select" (native single choice) or None
        when the option exposes no selection state (single choice card)."""
        for getter, kind in (("GetTogglePattern", "toggle"), ("GetSelectionItemPattern", "select")):
            method = getattr(node.control, getter, None)
            if method and method() is not None:
                return kind
        return None

    @staticmethod
    def enabled(node):
        try:
            return bool(node.control.IsEnabled)
        except Exception:
            return None

    @staticmethod
    def selected(node):
        """Read both multi-choice toggles and native single-choice controls."""
        for getter, prop in (("GetTogglePattern", "ToggleState"), ("GetSelectionItemPattern", "IsSelected")):
            method = getattr(node.control, getter, None)
            if method:
                pattern = method()
                if pattern is not None:
                    value = getattr(pattern, prop)
                    return value == 1 if prop == "ToggleState" else bool(value)
        return None


LIMIT_WINDOW = dt.timedelta(hours=5)   # a 5-hour limit cannot outlast its window
CLEAR_SCANS = 2                        # scans without an unknown-reset limit before it counts as gone
PERMISSION_CHECK_S = 3                 # between scans: how often a permission card is looked for
PERMISSION_BACKOFF_S = 30              # ...after an approval that did not go through


@dataclass
class SessionState:
    phase: str = "watching"
    reason: str = ""
    due: object = None
    reset: object = None
    attempts: int = 0
    question_id: str = ""
    notice: str = ""
    next_panel: float = 0
    notice_label: str = ""
    since: object = None        # first sighting of a limit whose reset is unknown
    clear_scans: int = 0        # consecutive scans without that limit
    not_before: object = None   # earliest next attempt after a send or a failed try


class SessionEngine:
    def __init__(self, worker, ui=None, quota=None):
        self.worker = worker
        self.ui = ui or ClaudeUI(worker)
        self.quota = quota or QuotaLog()
        self.handover = HandoverFlow(self)
        self.sessions = {}
        self.catalog = {}
        self.cursor = 0
        self.next_scan = 0
        self.next_permission = 0
        self.last_scan_error = ""

    def discover(self, root):
        """Open panes and sidebar entries of this app (ChatGPT's engine overrides it)."""
        return discover(root)

    def refresh(self):
        panes, entries = self.discover(self.ui.snapshot())
        previous = self.catalog
        self.catalog = {}
        # Follow Claude's current sidebar order instead of first-discovery order.
        # A newly opened chat may not have reached the sidebar yet: put it first.
        sidebar_keys = {entry["key"] for entry in entries}
        for pane in panes:
            if pane.key not in sidebar_keys:
                self.catalog[pane.key] = dict(key=pane.key, title=pane.title, kind=pane.kind,
                                              source="open", available=True)
        for entry in entries:
            self.catalog[entry["key"]] = {k: v for k, v in entry.items() if k != "node"}
            self.catalog[entry["key"]]["available"] = True
        for pane in panes:
            self.catalog[pane.key] = dict(key=pane.key, title=pane.title, kind=pane.kind,
                                          source="open", available=True)
        for key, item in previous.items():
            if key not in self.catalog:
                self.catalog[key] = dict(item, available=False)
        for key in self.worker.cfg.get("selected_chats", []):
            if key not in self.catalog and ":" in key:
                kind, title = key.split(":", 1)
                self.catalog[key] = dict(key=key, kind=kind, title=title, source="sidebar", available=False)
        self.publish()
        return panes

    def targets(self, panes):
        if self.worker.cfg.get("watch_scope", "open") == "selected":
            return list(dict.fromkeys(self.worker.cfg.get("selected_chats", [])))
        return list(dict.fromkeys(p.key for p in panes))

    def publish(self):
        rows = []
        for key, item in self.catalog.items():
            state = self.sessions.get(key, SessionState())
            selected = (key in self.worker.cfg.get("selected_chats", []) if self.worker.cfg.get("watch_scope") == "selected"
                        else item.get("source") == "open" and item.get("available"))
            phase = ("inactive" if not selected or self.worker.state == "IDLE" else
                     "starting" if self.worker.state == "STARTING" else state.phase)
            rows.append(dict(item, phase=phase, due=state.due, attempts=state.attempts,
                             notice=state.notice))
        self.worker.emit("chats", rows)
        pending = [s for s in self.sessions.values() if s.phase in ("waiting", "verifying") and s.due]
        nearest = min(pending, key=lambda s: s.due) if pending else None
        self.worker.reset_at = nearest.reset if nearest else None
        self.worker.send_at = nearest.due if nearest else None
        pause = getattr(self.worker, "pause_reason", None)
        shown = (self.worker.state if self.worker.state in ("IDLE", "STARTING") else
                 "PAUSED" if pause else "ARMED" if nearest else "MONITORING")
        self.worker.emit("state", dict(state=shown, reset_at=self.worker.reset_at, send_at=self.worker.send_at,
                                       pause_reason=pause,
                                       start_until=getattr(self.worker, "start_until", None)))

    def reset(self):
        self.sessions.clear()
        self.handover.clear()
        self.next_scan = 0

    def note(self, key, state, text, **details):
        """Log what happened to a conversation, once per change. `text` is a key or one of
        the program's own error messages; both reach the log in the window's language."""
        label = self.worker.text(text, **details)
        if state.notice != text or state.notice_label != label:
            state.notice, state.notice_label = text, label
            self.worker.log("log_chat_action", chat=key.split(":", 1)[-1], action=label)

    def logged_reset(self):
        try:
            return self.quota.latest_reset()
        except Exception:   # an unreadable log must never stop the watch
            return None

    def logged_context(self, title):
        """How much context this conversation has used, from the app's own log, when the
        window does not say. An unreadable log must never stop the watch."""
        try:
            return self.quota.context_for(title)
        except Exception:
            return None

    def arm(self, reset):
        panes = self.refresh()
        for key in self.targets(panes):
            self.sessions[key] = SessionState(phase="waiting", reason="limit", reset=reset,
                due=reset + dt.timedelta(seconds=self.worker.cfg["send_delay_after_reset_s"]))
        self.publish()

    def check_permissions(self):
        """Between scans, approve a waiting permission card within seconds. The cheap
        search almost always finds nothing; only a hit reads the whole window."""
        cfg = self.worker.cfg
        if not (cfg.get("auto_permissions") and cfg["auto_send"]) or time.monotonic() < self.next_permission:
            return
        self.next_permission = time.monotonic() + PERMISSION_CHECK_S
        try:
            if not self.ui.permission_waiting():
                return
            panes, _ = self.discover(self.ui.snapshot())
        except Exception:
            return      # the regular scan reports a window it cannot read
        targets = set(self.targets(panes))
        for pane in panes:
            card = permission_in(pane) if pane.key in targets else None
            if not card:
                continue
            try:
                with input_lock(worker_cancelled(self.worker)):
                    self.permit(pane.key, card)
            except Exception as exc:
                self.next_permission = time.monotonic() + PERMISSION_BACKOFF_S
                self.note(pane.key, self.sessions.setdefault(pane.key, SessionState()), str(exc))

    def tick(self, now=None):
        now = now or dt.datetime.now()
        # Someone else at the mouse (a person, or an agent driving the screen) means
        # hands off everything: reading is harmless, but there is nothing to do with a
        # reading we may not act on.
        reason = self.worker.guard.pause_reason()
        if reason:
            if self.worker.pause_reason != reason:
                self.worker.pause_reason = reason
                self.worker.log("log_paused", "warn", why=self.worker.text("pause_" + reason))
            self.publish()
            return
        if self.worker.pause_reason:
            self.worker.pause_reason = None
            self.worker.log("log_pause_over")
        self.check_permissions()
        if time.monotonic() < self.next_scan:
            return
        try:
            panes = self.refresh()
            self.last_scan_error = ""
        except Exception as exc:
            self.next_scan = time.monotonic() + self.worker.cfg["scan_interval_s"]
            hidden = isinstance(exc, WindowHidden)
            self.worker.emit("status", "hidden" if hidden else "no_window")
            if str(exc) != self.last_scan_error:
                self.last_scan_error = str(exc)
                if hidden:
                    self.worker.log("log_hidden", "warn")
                else:
                    self.worker.log("log_monitor_error", "warn", err=self.worker.text(str(exc)))
            return
        targets = self.targets(panes)
        self.sessions = {k: v for k, v in self.sessions.items() if k in targets}
        self.next_scan = time.monotonic() + max(2, self.worker.cfg["scan_interval_s"] / max(1, len(targets)))
        if not targets:
            self.publish()
            return
        key = targets[self.cursor % len(targets)]
        self.cursor += 1
        state = self.sessions.setdefault(key, SessionState())
        try:
            # Reading, clicking and typing for one conversation is one unit: the
            # other Auto-Resume must not bring its window forward in the middle.
            with input_lock(worker_cancelled(self.worker)):
                self.scan_one(key, state, now)
        except Exception as exc:
            self.note(key, state, str(exc))
        finally:
            self.publish()

    def scan_one(self, key, state, now):
        """Observe one conversation and act on it (called with the input lock held)."""
        from claude_auto_continue import detect_limit_banner, parse_reset_time
        pane = self.ui.resolve(key, navigate=self.worker.cfg.get("watch_scope") == "selected")
        if pane is None:
            self.note(key, state, "unavailable")
            return
        observed = signals(pane, detect_limit_banner)
        self.worker.emit("status", "ok")
        threshold = self.worker.cfg.get("limit_threshold_pct", 100)
        meter = usage_meter(pane, parse_reset_time)
        maxed = meter["pct"] is not None and meter["pct"] >= threshold
        logged = self.logged_reset() if observed["limit"] or maxed else None
        if observed["limit"]:
            # The notice often omits the time; the meter (when maxed) and
            # Claude Code's own log of the rejection still carry it.
            observed["reset"] = choose_reset(
                [observed["reset"], meter["reset"] if maxed else None, logged], now)
        # A maxed meter alone is ambiguous (it shows the fullest plan window):
        # only the usage panel tells the 5-hour row from a weekly one.
        need_panel = (observed["limit"] and not observed["reset"]) or (not observed["limit"] and maxed)
        unknown = state.phase == "waiting" and state.reason == "limit" and state.reset is None
        if need_panel and time.monotonic() >= state.next_panel and (state.phase == "watching" or unknown):
            state.next_panel = time.monotonic() + self.worker.cfg.get("panel_backoff_s", 300)
            rows, ok = self.worker._read_usage_panel(self.worker._get_window(), meter_scope=pane.root.control)
            if ok:
                self.worker.emit("usage", {"rows": rows, "session": pane.title})
                h5 = rows.get("5h", {})
                if observed["limit"] or h5.get("pct", 0) >= threshold:
                    observed["limit"] = True
                    observed["reset"] = choose_reset([observed["reset"], h5.get("reset"), logged], now)
        self.step(key, state, observed, now)
        self.after_observe(key, state, observed, now, pane)

    def after_observe(self, key, state, observed, now, pane):
        """Shared tail of a scan: hand the conversation over when its context is full.
        A conversation the scheduler is busy with (a limit, an error, a question) is only
        waited for; the handover never talks over it."""
        self.handover.step(key, state, observed, now, pane, state.phase in BLOCKED_PHASES)

    def permit(self, key, card):
        """Approve a permission card when that option is on ("Send automatically" is the
        master switch) and log what was allowed."""
        cfg = self.worker.cfg
        if not (cfg.get("auto_permissions") and cfg["auto_send"]):
            return
        choice = self.ui.approve(key, card)
        if choice:
            self.worker.log("log_chat_action", chat=key.split(":", 1)[-1],
                            action=self.worker.text("permission_" + choice, title=card["title"]))

    def step(self, key, state, observed, now):
        cfg = self.worker.cfg
        if observed.get("permission"):
            # Claude waits for a permission: nothing is typed meanwhile and the
            # schedule stays as it was.
            self.permit(key, observed["permission"])
            return
        if state.phase == "exhausted":
            return
        if observed["permanent"] and cfg.get("retry_api_errors"):
            state.phase, state.due = "exhausted", None
            self.note(key, state, "permanent_error")
            return
        if observed["question"]:
            state.phase, state.due = "question", None
            qid = observed["question"]["fingerprint"]
            if cfg.get("auto_approach") and cfg["auto_send"] and qid != state.question_id:
                # Mark before side effects: an uncertain Submit must not be repeated.
                state.question_id = qid
                clicks = getattr(self.ui, "clicks", None)
                try:
                    answered = self.ui.answer(key, qid)
                except Exception:
                    if clicks is not None and self.ui.clicks == clicks:
                        state.question_id = ""   # nothing was clicked: the next scan may try again
                    raise
                if answered:
                    self.note(key, state, "approach_submitted")
            return
        if state.phase == "question":
            state.phase = "watching"
        state.question_id = ""
        if observed["limit"] and state.reason != "limit":
            # A rate-limit notice supersedes a previously scheduled short API
            # retry, even if that retry became due during this scan.
            state.phase = "watching"
        if state.phase == "verifying" and state.due and now < state.due:
            return
        if state.phase == "verifying":
            if not observed["limit"] and not observed["error"] and not observed["retry"]:
                state.phase, state.reason, state.due, state.reset = "watching", "", None, None
                state.attempts, state.since, state.clear_scans, state.not_before = 0, None, 0, None
                self.note(key, state, "resumed")
                return
            state.phase = "watching"
        if state.phase == "watching":
            if observed["limit"]:
                state.reason, state.reset, state.since, state.clear_scans = "limit", None, now, 0
            elif cfg.get("retry_api_errors") and (observed["error"] or observed["retry"]):
                state.reason = "api"
                state.due = now + dt.timedelta(seconds=min(900, cfg.get("api_retry_wait_s", 30) * 2 ** state.attempts))
                self.note(key, state, "waiting_api")
            else:
                return
            state.phase = "waiting"
        if state.phase != "waiting":
            return
        if state.reason == "limit":
            if not self.limit_due(key, state, observed, now):
                return
        elif now < state.due:
            return
        if state.reason == "api" and not cfg.get("retry_api_errors"):
            state.phase, state.due = "watching", None
            return
        if not cfg["auto_send"]:
            state.phase = "exhausted"
            self.worker.emit("beep")
            self.note(key, state, "alert_only")
            return
        if state.attempts >= cfg["max_retries"]:
            state.phase = "exhausted"
            self.note(key, state, "retry_limit")
            return
        # If the user already resumed the chat, don't inject another message.
        if observed["busy"]:
            state.phase, state.due = "watching", None
            return
        if state.reason == "api" and not (observed["error"] or observed["retry"]):
            state.phase, state.due = "watching", None
            return
        state.attempts += 1
        state.phase = "verifying"
        state.due = now + dt.timedelta(seconds=cfg.get("verify_delay_s", 30))
        # With the context over the threshold, the message that resumes a blocked chat is
        # the request for a handover, not the user's text (and never the Try again button).
        message = self.handover.resume_text(key, state, observed, now)
        try:
            result = self.ui.resume(key, cfg.get("prefer_try_again", False) and not message,
                                    state.reason == "api", message=message)
        except Exception:
            state.phase = "waiting"
            state.due = state.not_before = now + dt.timedelta(seconds=max(30, cfg.get("api_retry_wait_s", 30)))
            raise
        if message:
            self.handover.resume_sent(key, now)
        if state.reason == "limit":
            # If the chat is still blocked afterwards, a stale reset must not
            # trigger another message on the very next scan.
            state.not_before = now + dt.timedelta(seconds=cfg["retry_wait_s"])
        self.note(key, state, result)

    def limit_due(self, key, state, observed, now):
        """Schedule a limit continuation and say whether it may be typed now.

        Never before a known reset (+ send delay). With the reset unknown, never
        while the limit is still on screen: only once it has been gone for
        CLEAR_SCANS scans, or when the 5-hour window must have ended. (15.09: the
        reset time was unknown and blind retries typed three messages into a
        session that stayed blocked.)"""
        cfg = self.worker.cfg
        delay = dt.timedelta(seconds=cfg["send_delay_after_reset_s"])
        if observed["limit"] and observed["reset"]:
            state.reset = observed["reset"]   # newest reading wins, e.g. a moved reset
        if state.reset:
            state.due = max(state.reset + delay, state.not_before or state.reset + delay)
            self.note(key, state, "waiting_limit_at", reset=f"{state.reset:%H:%M}", send=f"{state.due:%H:%M:%S}")
            return now >= state.due
        state.since = state.since or now
        cap = state.since + LIMIT_WINDOW + delay
        state.due = max(cap, state.not_before or cap)
        self.note(key, state, "waiting_limit_unknown", send=f"{state.due:%H:%M:%S}")
        state.clear_scans = 0 if observed["limit"] else state.clear_scans + 1
        gone = state.clear_scans >= CLEAR_SCANS and now >= (state.not_before or now)
        return gone or now >= state.due
