"""Scoped Claude accessibility adapter and independent per-conversation scheduler.

No coordinates or UIA objects are persisted. Every action resolves its conversation
again; ambiguous titles, drafts and unknown controls fail closed.
"""
from _ctypes import COMError
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import os
import re
import time

from handover import BLOCKED_PHASES, HandoverFlow, HandoverRefused, context_from_meter, git_branch
from input_guard import InputPaused
from input_lock import input_lock, worker_cancelled
from quota_log import QuotaLog


PROMPTS = {"prompt", "write your prompt to claude", "reply to claude", "message claude"}
RETRY = {"try again", "retry", "spróbuj ponownie", "ponów"}
SUBMIT = {"submit", "wyślij", "zatwierdź"}
# The card's main button reads "Next" on every question but the last one.
PRIMARY = SUBMIT | {"next", "dalej"}
QUESTION_CONTROLS = PRIMARY | {"dismiss question", "view question options", "skip", "back",
                               "next question", "previous question",
                               # 2.37 pages through a card with several questions (15.10)
                               "show next question", "show previous question"}
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
# Claude 2.37's new-conversation screen names its folder on this button (15.10).
ENVIRONMENT = re.compile(r"^Environment:\s*(?P<name>.+?)\s*$", re.I)
NEW_TITLE_WAIT_S = 40
SECOND_LOOK_S = 1.0         # before an empty page counts as a covered window
# Buttons of the permission card ("Allow Claude to fetch …?") end with their key digit,
# e.g. "Allow once 2" (captured 25.09); key names are stripped too, should a version
# spell them out. The allow buttons in order of preference.
KEY_HINTS = re.compile(r"(?:\s*(?:\d+|ctrl|shift|alt|esc|enter|return|[⏎↵⌘⇧⌃⌥+]))+\s*$", re.I)
ALLOW = ("always allow", "allow once")
# The box a turn ends with (16.10) holds its buttons in this group. A button may be one of
# the app's own actions instead of an answer: those act outside the conversation (git,
# GitHub, archiving, new sessions) and stay with the person. Their labels were never
# measured, so anything that reads like one is held back, a reply about committing too.
NEXT_ACTIONS = "Suggested next actions"
APP_ACTION = re.compile(r"commit|merg|archiv|archiw|pull\s+request|\bpr\b|\bfix\s+ci\b|conflict|comment|"
                        r"\bverify\b|\bnew\s+session\b", re.I)
# The card about a new session (16.10): "1 suggested task", headed by one of these texts.
SUGGESTED_TASKS = re.compile(r"^\d+ suggested tasks?$", re.I)
CARD_HEADERS = {"new session", "start this in its own session?"}
# The sidebar row of a session started from such a card: "<title> Started by Claude from …".
STARTED_ROW = re.compile(r" Started (?:by Claude from|from a suggestion in) ")
# ...the same line under the row's title names the conversation it was started from, in full
# (20.10), whoever started it: this program, you, or Claude itself.
STARTED_FROM = re.compile(r"^Started (?:by Claude from|from a suggestion in) (?P<parent>.+)$")
# What a row's status bar says of a session that needs no watching: nothing, or a finished
# turn nobody has read yet (20.10). "Running" and "Needs input" are the other kinds.
SETTLED = {"", "unread response"}
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


MODE_KINDS = {"claude": "chat", "chat and cowork": "chat", "code": "code"}


def mode_kind(name):
    """"chat" or "code" for a mode switch's name ("Code, working" while that mode works)."""
    return MODE_KINDS.get(name.split(",")[0].strip().casefold())


def sidebar_mode(nodes):
    """Which list the sidebar shows, from the selected mode switch: Claude 2.37 has radio
    buttons "Claude" / "Code" in a "Mode" group (15.10). None without one."""
    for n in nodes:
        if n.type == "RadioButtonControl" and mode_kind(n.name) and n.inside("mode") and ClaudeUI.selected(n):
            return mode_kind(n.name)
    return None


def discover(root):
    """Return named panes and sidebar entries. Duplicate keys remain ambiguous."""
    nodes = list(root.walk())
    mode = sidebar_mode(nodes)
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
        kind = mode or ("chat" if any(n.name == "Chats and tasks" for n in bar_nodes) else "code")
        # Claude exposes the full title in the companion menu button, even when
        # the displayed text is truncated. The row itself is a different button: named
        # "Running <title>" in older builds, "<title>" or "<title> Started from a
        # suggestion in …" in 2.37.
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
                           (n.name == title or n.name.endswith(" " + title) or
                            n.name.startswith(title + " ")) and
                           not n.name.startswith("More options for ")]
                if matches:
                    row = matches[0] if len(matches) == 1 else None
                    break
            if row:
                entries.append(dict(key=key_for(kind, title), title=title,
                                    kind=kind, node=row, source="sidebar"))
    return panes, entries


def started_from(row):
    """The title of the conversation a sidebar row's session was started from, None for a
    session started on its own: the row says "Started by Claude from <title>" under its
    own title (20.10)."""
    for n in row.walk():
        match = STARTED_FROM.match(n.name.strip()) if n.type == "TextControl" else None
        if match:
            return match["parent"]
    return None


def row_status(row):
    """What a sidebar row's status bar says beside the title (20.10): "Running", "Needs
    input", "Unread response", or "" when it says nothing."""
    for bar in row.walk():
        if bar.type == "StatusBarControl":
            return next((n.name.strip() for n in bar.walk() if n.type == "TextControl" and n.name.strip()), "")
    return ""


def question_in(pane):
    """Only the live question widget; never infer choices from chat prose."""
    return docked_question(pane) or inline_question(pane)


def transcript_entries(pane):
    """The loaded entries of the transcript, in order. Older builds list the messages as
    articles right in "Chat messages", beside whatever else it holds; Claude 2.37 puts them
    in a list of its own (15.10)."""
    feed = next((n for n in pane.root.walk() if n.name.casefold() == "chat messages"), None)
    if feed is None:
        return []
    articles = [n for n in feed.children if n.role == "article"]
    if articles:
        return articles
    # The list holds entries and "Load …" buttons, never texts of its own (a card does).
    lists = [n for n in feed.children if n.children and not any(c.type == "TextControl" for c in n.children)]
    return max(lists, key=lambda n: len(n.children)).children if lists else []


def live_turn(pane):
    """The end of the transcript, where Claude 2.37 shows its cards: every loaded entry after
    your last message. Not just the last article: a long turn comes in parts, the first one
    an article ("Currently streaming message") and the rest plain groups behind "Load the
    rest of this message", and older parts are not loaded at all (15.10). None when nothing
    follows your message. The entries keep their real parents."""
    entries = transcript_entries(pane)
    start = 0
    for i, entry in enumerate(entries):
        if any(n.name.startswith("You said:") for n in entry.walk()):
            start = i + 1
    tail = entries[start:]
    return Node("", "GroupControl", children=list(tail)) if tail else None


def item_label(node):
    """An option's or a menu item's own label: its first text, else its name."""
    return next((n.name for n in node.walk() if n.type == "TextControl" and n.name.strip()), node.name).strip()


def inline_question(pane):
    """Claude 2.37 asks in the transcript's last message (15.10): a group named after the
    question holds one toggle button per option (its label, then a description), "Other"
    (whose text box appears once it is picked), "Skip" and "Submit" / "Next". The first
    option starts out picked, so a pick tells nothing about the person."""
    turn = live_turn(pane)
    if turn is None:
        return None
    cards = []
    for group in turn.walk():
        if group.type != "GroupControl" or group is turn:
            continue
        buttons = [n for n in group.walk() if n.type == "ButtonControl"]
        names = [n.name.strip().casefold() for n in buttons]
        submit = [n for n, name in zip(buttons, names) if name in PRIMARY]
        options = [n for n, name in zip(buttons, names) if name not in QUESTION_CONTROLS]
        if len(submit) == 1 and "skip" in names and len(options) >= 2:
            cards.append((len(list(group.walk())), group, options, submit[0]))
    if not cards:
        return None
    _, group, options, submit = min(cards, key=lambda card: card[0])   # the card itself, not a wrapper
    labels = [item_label(option) for option in options]
    recommended = [option for option, label in zip(options, labels)
                   if RECOMMENDED.search(label) and not NEGATIVE.search(label)]
    other = [n for n in group.walk() if n.type == "EditControl" and n.name.casefold() in {"other option", "other", "inna odpowiedź"}]
    # What does not change while options are picked or "Other" opens its box.
    hidden = {n for holder in [*options, *(n for n in group.walk() if n.type == "EditControl")] for n in holder.walk()}
    texts = [n.name for n in group.walk() if n.type == "TextControl" and n not in hidden]
    identity = "\n".join([*texts, *labels, submit.name])
    return dict(group=group, options=options, labels=labels, recommended=recommended,
                other=other[0] if len(other) == 1 else None, submit=submit, inline=True,
                fingerprint=hashlib.sha256(identity.encode()).hexdigest())


def docked_question(pane):
    """The question card of older builds: docked above the composer, with "Dismiss question"."""
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
    """True for a name like the permission card's allow buttons: "Allow once 2", or in
    Claude 2.37 "Always allow , Applies to this session only"."""
    return KEY_HINTS.sub("", name.split(",")[0]).strip().casefold() in ALLOW


def button_label(node):
    """A button's own label: its first text, else its name without the key that
    Claude's cards append ("Allow once 2" -> "Allow once")."""
    text = next((n.name for n in node.walk() if n.type == "TextControl" and n.name.strip()), None)
    return (text if text is not None else KEY_HINTS.sub("", node.name)).strip()


def permission_in(pane):
    """The live permission card: Deny plus "Always allow" and/or "Allow once", docked
    above the composer, or in Claude 2.37 inside the transcript's last message ("Permission
    request: write", 15.10). A question card never counts, even when its answers read like
    permission buttons."""
    composer_line = set(pane.prompt.ancestors())
    turn = live_turn(pane)
    entries = set(turn.children) if turn is not None else set()
    for deny in [*pane.ui_nodes(), *(turn.walk() if turn is not None else ())]:
        if deny.type != "ButtonControl" or button_label(deny).casefold() != "deny":
            continue
        # The card is the widest ancestor that does not also hold the composer, nor
        # reach past its own message (or its part of a long one).
        card = deny
        for ancestor in deny.ancestors():
            if ancestor in composer_line or ancestor.role == "article":
                break
            card = ancestor
            if ancestor in entries or ancestor.name.strip().casefold().startswith("permission request"):
                break
        buttons = [n for n in card.walk() if n.type == "ButtonControl"]
        names = {n.name.strip().casefold() for n in buttons}
        if "dismiss question" in names or ("skip" in names and names & PRIMARY):
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
        # The question opens the card as a run of texts: "Allow Claude to ", "run", " ",
        # what, "?" — in 2.37 without the space ("write", "permission-test.txt").
        title = ""
        for child in card.children:
            if child.type != "TextControl":
                break
            part = child.name
            if title and part and not title[-1].isspace() and not part[0].isspace() and part[0] not in "?.,:;!":
                title += " "
            title += part
        identity = "\n".join([*texts, *labels])
        return dict(card=card, always=next(iter(found["always allow"]), None),
                    once=next(iter(found["allow once"]), None),
                    title=" ".join(title.split()) or next(iter(texts), ""),
                    fingerprint=hashlib.sha256(identity.encode()).hexdigest())
    return None


def shortcut(node):
    """The key a button answers to ("Control+Enter" on a card's main button, 16.10)."""
    try:
        return node.control.AcceleratorKey or ""
    except (AttributeError, COMError):
        return ""


def next_step_in(pane):
    """The box a turn ends with when Claude asks how to go on (16.10): the recap, the
    question and "Suggested next actions" with one or two buttons. The main button is the
    one with Ctrl+Enter, else the first; its label is the agent's own words, in any
    language. Two buttons with Ctrl+Enter are not a box we understand."""
    turn = live_turn(pane)
    groups = [n for n in (turn.walk() if turn is not None else ())
              if n.type == "GroupControl" and n.name == NEXT_ACTIONS]
    if len(groups) != 1 or groups[0].parent is None:
        return None
    actions, box = groups[0], groups[0].parent
    buttons = [n for n in actions.walk() if n.type == "ButtonControl"]
    marked = [b for b in buttons if shortcut(b) == "Control+Enter"]
    if not buttons or len(marked) > 1:
        return None
    primary = marked[0] if marked else buttons[0]
    in_actions = set(actions.walk())
    texts = [n.name for n in box.walk() if n.type == "TextControl" and n.name.strip() and n not in in_actions]
    identity = "\n".join([*texts, *(b.name for b in buttons)])
    return dict(group=box, buttons=buttons, primary=primary, label=primary.name.strip(),
                action=bool(APP_ACTION.search(primary.name)),
                fingerprint=hashlib.sha256(identity.encode()).hexdigest())


def session_offer_in(pane):
    """The card about a new session (16.10), in the last turn as "1 suggested task": a
    suggestion the agent makes by itself (Start / Do it here) or its question whether to
    do a task here or in a session of its own (New session / Do it here). A group with
    more than one task is left alone."""
    turn = live_turn(pane)
    groups = [n for n in (turn.walk() if turn is not None else ())
              if n.type == "GroupControl" and SUGGESTED_TASKS.match(n.name.strip())]
    if len(groups) != 1:
        return None
    group = groups[0]
    buttons = [n for n in group.walk() if n.type == "ButtonControl"]
    labels = [b.name.strip().casefold() for b in buttons]
    start = [b for b, label in zip(buttons, labels) if label in ("start", "new session")]
    here = [b for b, label in zip(buttons, labels) if label == "do it here"]
    if len(start) != 1 or len(here) != 1:
        return None
    kind = "offer" if "new session" in labels else "suggestion"
    title = next((n.name.strip() for n in group.walk() if n.type == "TextControl"
                  and n.name.strip() and n.name.strip().casefold() not in CARD_HEADERS), "")
    identity = "\n".join([kind, title, *(b.name for b in buttons)])
    return dict(group=group, kind=kind, title=title, start=start[0], here=here[0],
                fingerprint=hashlib.sha256(identity.encode()).hexdigest())


def final_limit_card(pane, parse):
    """Claude Code ends a blocked conversation with a card in the transcript:
    "Session limit reached" / "Try again after your session limit resets." /
    View details / Try again (older cards collapse to a "Session limit reached"
    button). Only the transcript's last entry counts: earlier cards stay after a resume,
    and after "Try again" the retried work follows the card without a message of yours.
    Returns (found, reset_or_None)."""
    turn = live_turn(pane)
    entries = [e for e in (turn.children if turn is not None else ()) if any(n.name.strip() for n in e.walk())]
    if not entries:
        return False, None
    nodes = list(entries[-1].walk())       # never your message: the turn begins after it
    headlines = [n for n in nodes if LIMIT_HEADLINE.fullmatch(n.name.strip())]
    collapsed = any(n.type == "ButtonControl" for n in headlines)
    actions = any(n.type == "ButtonControl" and n.name.strip().casefold() in CARD_ACTIONS for n in nodes)
    if not headlines or not (collapsed or actions):
        return False, None
    resets = (parse(n.name) for n in nodes if n.type == "TextControl" and "reset" in n.name.casefold())
    return True, next((r for r in resets if r), None)


RAIL_SHARE = re.compile(r"^context\s+(\d{1,3})\s*%$", re.I)        # the folded section: "Context 44%"
RAIL_USED = re.compile(r"^(\d{1,3})\s*%\s+used$", re.I)             # the open one: "45% used"
RAIL_TOKENS = re.compile(r"^\d+(?:[.,]\d+)?\s*[km]?\s*/\s*\d+(?:[.,]\d+)?\s*[km]?$", re.I)   # "453.6k / 1M"


def context_from_rail(pane):
    """The context reading of the "Session details" panel beside a Claude 2.37 conversation
    (15.10): "Context 44%" while its section is folded, "45% used" and "453.6k / 1M" once it
    is open. None when the panel is hidden or shows no context (an idle conversation)."""
    share = tokens = None
    for n in pane.ui_nodes():
        if not n.inside("session details"):
            continue
        name = n.name.strip()
        found = RAIL_SHARE.match(name) or (RAIL_USED.match(name) if n.inside("context") else None)
        if found and share is None:
            share = found.group(1)
        if tokens is None and n.inside("context") and RAIL_TOKENS.match(name):
            tokens = name
    if tokens:
        return context_from_meter("Context %s%s" % (tokens, " (%s%%)" % share if share else ""))
    return context_from_meter("Context %s%%" % share) if share else None


def merged_context(reading, logged):
    """The window's context reading, with the token count from the conversation's own log
    when the window shows only a share (or nothing)."""
    if not logged or (reading and reading.get("used") is not None):
        return reading
    window = (reading or {}).get("window") or logged.get("window")
    share = (reading or {}).get("pct")
    if share is None and window:
        share = round(100 * logged["used"] / window)
    return dict(used=logged["used"], window=window, pct=share)


MODEL_BUTTON = re.compile(r"^Model:\s*(?P<rest>.+?)\s*$", re.I)
EFFORT_BUTTON = re.compile(r"^Effort:\s*(?P<effort>.+?)\s*$", re.I)
PERMISSIONS_BUTTON = re.compile(r"^Permissions:\s*(?P<mode>.+?)\s*$", re.I)
MODES = {"auto", "manual", "accept edits", "plan", "bypass permissions"}


def chat_settings(nodes):
    """Model, effort and permission mode as the bar under a composer shows them (Claude
    2.37, 15.10): "Model: Opus 5.5 Max …" holds the model and the effort as texts of their
    own; the mode is the button beside it ("Bypass permissions" in a conversation,
    "Permissions: Manual" on the new-conversation screen). Older builds had a separate
    "Effort: Extra" button. What the bar does not show is ""."""
    nodes = [n for n in nodes if n.type == "ButtonControl"]
    settings = dict(model="", effort="", mode="")
    model = next((n for n in nodes if MODEL_BUTTON.match(n.name.strip())), None)
    if model is None:
        return settings
    texts = [n.name.strip() for n in model.walk() if n.type == "TextControl" and n.name.strip()]
    settings["model"] = texts[0] if texts else MODEL_BUTTON.match(model.name.strip())["rest"]
    if len(texts) > 1:
        settings["effort"] = texts[1]
    for n in nodes:
        if abs(n.rect[1] - model.rect[1]) > 12:
            continue                  # only the composer's own bar
        name = n.name.strip()
        effort = EFFORT_BUTTON.match(name)
        if effort and not settings["effort"]:
            settings["effort"] = effort["effort"]
        permissions = PERMISSIONS_BUTTON.match(name)
        mode = permissions["mode"] if permissions else name
        if mode.casefold() in MODES and not settings["mode"]:
            settings["mode"] = mode
    return settings


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
    so a message of ours must never count as an answer. A long reply comes in parts
    (Claude 2.37): the marker closes the last one.
    """
    turn = live_turn(pane)
    if turn is None:
        return ""
    nodes = list(turn.walk())
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
    from claude_auto_continue import detect_wrap_up, parse_reset_time
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
    # The box and the new-session card end a turn with the agent's own words, which may
    # well mention an error: they are never the error itself.
    next_step, offer = next_step_in(pane), session_offer_in(pane)
    own_words = {n for card in (next_step, offer) if card for n in card["group"].walk()}
    chat_nodes = [n for n in pane.root.walk() if n.inside("chat messages")]
    leaves = [n for n in chat_nodes if n.type == "TextControl" and n.name.strip() and n not in own_words
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
    # The meter carried the context until Claude 2.37 moved it to the session panel.
    return dict(context=(context_from_meter(meter.name) if meter is not None else None) or context_from_rail(pane),
                last_text=last_reply_text(pane),
                limit=bool(banner) or card, reset=reset or card_reset, error=error,
                # A wrap-up ends the turn with Claude's own reply; a hard stop ends it
                # with the card. The resume text differs.
                wrap_up=bool(banner) and detect_wrap_up(texts), card=card,
                permanent=bool(error and PERMANENT_ERROR.search(error)),
                retry=retries[0] if len(retries) == 1 else None,
                busy=any(n.type == "ButtonControl" and n.name.casefold() in
                         {"stop", "stop response", "stop generating", "zatrzymaj"} for n in ui),
                question=question, permission=permission, next_step=next_step, offer=offer)


class ClaudeUI:
    def __init__(self, worker):
        self.worker = worker
        self.clicks = 0     # clicks attempted; tells whether a failed action had any effect

    def snapshot(self):
        win = self.worker._get_window()
        if win is None:
            raise RuntimeError("Claude window is unavailable")
        for look in range(2):
            self.worker._wake_accessibility(win)
            root = self._read(win)
            if not content_hidden(root):
                return root
            # Chromium builds the page only once someone asks: a window nobody has read for
            # a while can still show an empty page right after the wake (ChatGPT, 15.10).
            if look == 0:
                time.sleep(SECOND_LOOK_S)
        raise WindowHidden("The app window is covered or minimized")

    def _read(self, win):
        """The window's accessibility tree as Nodes, read once."""
        from claude_auto_continue import auto
        root = Node("window", "WindowControl", control=win)
        stack = [(-1, root)]
        started = time.monotonic()
        try:
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
        except COMError:
            # Claude 2.37 rebuilds parts of the page while a conversation opens (15.10).
            raise RuntimeError("Accessibility tree changed; retry on next scan")
        if not any(n.type == "DocumentControl" for n in root.walk()):
            # No page yet (Claude 2.37 right after it starts): an empty window would read
            # as "no conversations".
            raise RuntimeError("Accessibility scan incomplete; no action taken")
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
            switches = [n for n in root.walk() if n.inside("sidebar") and (
                (n.type == "ButtonControl" and (n.name == label or n.name.startswith(label + ","))) or
                (n.type == "RadioButtonControl" and n.inside("mode") and mode_kind(n.name) == kind))]
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
            try:
                panes, _ = discover(self.snapshot())
            except RuntimeError:
                continue          # the page is being rebuilt while the chat opens
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

        It has no title yet, so `discover` does not see it as a pane. Claude 2.37 (15.10)
        names the folder on an "Environment: <folder>" button under the composer. The
        layout measured on 26.09: the group that holds the composer also holds, in this
        order, where to run it, the project, the branch box, "worktree" and "Add another
        folder" — so the project is the last button before the branch box.
        """
        panes = self.panes(root)
        taken = {pane.prompt for pane in panes}
        prompts = [n for n in root.walk() if n.type == "EditControl" and n.visible
                   and n.name.casefold() in PROMPTS and n not in taken]
        if len(prompts) != 1:
            return None, None
        prompt = prompts[0]
        in_panes = {n for pane in panes for n in pane.root.walk()}
        environments = [n for n in root.walk() if n.type == "ButtonControl" and n.visible
                        and ENVIRONMENT.match(n.name.strip()) and n not in in_panes]
        if len(environments) == 1:
            return prompt, environments[0]
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

    def project_name(self, button):
        """The folder the new-conversation screen will work in, as its button shows it."""
        match = ENVIRONMENT.match(button.name.strip())
        return match["name"] if match else button.name.strip()

    @staticmethod
    def new_chat_buttons(root):
        return [n for n in root.walk() if n.type == "ButtonControl" and n.visible
                and n.name.strip().casefold() in NEW_CHAT_LABELS and n.inside("sidebar")]

    @staticmethod
    def one_new_button(buttons):
        """The one button that starts a conversation. Claude 2.37 adds a small "New session"
        icon beside the "Today" heading (15.10); the sidebar's own, wide button wins."""
        if len(buttons) == 1:
            return buttons[0]
        wide = [b for b in buttons if b.rect[2] - b.rect[0] >= 60]
        return wide[0] if len(wide) == 1 else None

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
        folder names, so it is compared with the folder the handover was written in.
        Claude 2.37 calls them environments ("auto-resume , edit environment, right
        arrow": the first text is the folder) and keeps the older ones under "More N"."""
        return self.choose(button, wanted)

    def choose(self, button, wanted, submenu=None, loose=False, prefix=False):
        """Pick the item labelled `wanted` in the menu that `button` opens; with `submenu`,
        inside the item that starts with it ("Effort"). An item not in the menu is looked
        for once under a single "More …" item. `loose` also takes an item whose name only
        starts with the label ("Fable 5.1 Requires usage credits"); never for folders,
        where "prep" must not open "prep it". `prefix` takes the item whose label begins
        `wanted` ("5.6 Terra" for ChatGPT's "5.6 Terra Wysoki"), the longest one. Two
        matches are never guessed between, and no menu is left open."""
        wanted = wanted.casefold()

        def match(n):
            label, name = item_label(n).casefold(), n.name.strip().casefold()
            if prefix:
                return wanted == label or wanted.startswith(label + " ")
            return label == wanted or (loose and (name == wanted or name.startswith(wanted + " ")))
        self.click(button)
        opened, reads = [], 0
        for _ in range(8):
            time.sleep(0.4)
            root = self.snapshot()
            menus = [n for n in root.walk() if n.type == "MenuControl" or n.role == "menu"]
            entries = list({id(n): n for menu in menus for n in menu.walk()
                            if n.type in ("RadioButtonControl", "MenuItemControl", "ListItemControl")
                            and n.visible}.values())
            heads = [n for n in entries if n.type == "MenuItemControl"]
            if submenu and submenu not in opened:
                head = [n for n in heads if item_label(n).casefold().startswith(submenu)]
                if len(head) == 1:
                    self.click(head[0])
                    opened.append(submenu)
                continue
            items = [n for n in entries if match(n) and not (submenu and item_label(n).casefold().startswith(submenu))]
            if prefix and items:
                longest = max(len(item_label(n)) for n in items)
                items = [n for n in items if len(item_label(n)) == longest]
            if len(items) == 1:
                self.click(items[0])
                time.sleep(0.8)
                return True
            if items:
                break                 # more than one match: never guess
            more = [n for n in heads if item_label(n).casefold().startswith("more")]
            if menus and len(more) == 1 and "more" not in opened:
                self.click(more[0])   # the older ones, one level down
                opened.append("more")
                continue
            if menus:
                reads += 1
                if reads >= 2:
                    break             # the menu is open and the item is not in it
        for _ in range(1 + len(opened)):
            self.press("{Esc}")       # never leave a menu open behind us
        return False

    def screen_nodes(self, root):
        """The new-conversation screen: everything outside the open conversations."""
        in_panes = {n for pane in self.panes(root) for n in pane.root.walk()}
        return [n for n in root.walk() if n not in in_panes]

    def apply_settings(self, wanted):
        """Give the new conversation the permission mode, model and effort of the one it
        takes over from (Claude 2.37 screen: "Permissions: …", and "Model: …" with an
        "Effort" submenu). Returns what could not be set; the handover goes on without it,
        with the app's own default for that part."""
        missed = []
        for part, prefix, submenu in (("mode", PERMISSIONS_BUTTON, None), ("model", MODEL_BUTTON, None),
                                      ("effort", MODEL_BUTTON, "effort")):
            want = (wanted.get(part) or "").strip()
            if not want:
                continue
            screen = self.screen_nodes(self.snapshot())
            if chat_settings(screen)[part].casefold() == want.casefold():
                continue
            buttons = [n for n in screen if n.type == "ButtonControl" and n.visible and prefix.match(n.name.strip())]
            try:
                done = len(buttons) == 1 and self.choose(buttons[0], want, submenu=submenu, loose=True)
                done = done and chat_settings(self.screen_nodes(self.snapshot()))[part].casefold() == want.casefold()
            except RuntimeError:
                done = False
            if not done:
                missed.append(part)
        return missed

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
        settings = chat_settings(pane.ui_nodes())     # how the old conversation works
        self.click(pane.prompt)       # this pane's turn: the new conversation opens here
        root = self.snapshot()
        before = {p.key for p in self.panes(root)}
        button = self.one_new_button(self.new_chat_buttons(root))
        if button is None:
            # A narrow window keeps the sidebar folded away, and with it the button that
            # starts a conversation. Unfold it and look again.
            root = self.show_sidebar(root)
            button = self.one_new_button(self.new_chat_buttons(root))
        if button is None:
            raise HandoverRefused("Cannot tell which button starts a new conversation")
        self.click(button)
        prompt, project = None, None
        for _ in range(6):
            time.sleep(0.5)
            try:
                prompt, project = self.new_session_screen(self.snapshot())
            except RuntimeError:
                continue          # the page is being rebuilt while the screen opens
            if prompt is not None and project is not None:
                break
        if prompt is None or project is None:
            raise HandoverRefused("The new conversation's screen could not be read")
        if self.project_name(project).casefold() != wanted:
            if not self.pick_project(project, wanted):
                raise HandoverRefused("The project of the handover is not on the list")
            prompt, project = self.new_session_screen(self.snapshot())
            if prompt is None or project is None or self.project_name(project).casefold() != wanted:
                raise HandoverRefused("The new conversation is not in the project of the handover")
        # The folder is right (and with it the branch it has checked out): now the model,
        # the effort and the permission mode, which never stop the handover.
        missed = self.apply_settings(settings)
        self.last_settings = dict(folder=project_root, branch=git_branch(project_root), **settings, missed=missed)
        prompt, _ = self.new_session_screen(self.snapshot())
        if prompt is None:
            raise HandoverRefused("The new conversation's screen could not be read")
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
        if q.get("inline"):
            return self._answer_inline(key, fingerprint, q)
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

    def _answer_inline(self, key, fingerprint, q):
        """Claude 2.37's card: pick exactly the recommended options (the ones wanted first,
        so a single-choice card moves its pick instead of being emptied), check, then Submit
        / Next. Without one recommendation a single-choice card cannot take them all: then
        Claude is asked to choose, through Other."""
        chosen = [label for option, label in zip(q["options"], q["labels"]) if option in q["recommended"]]
        if not chosen:
            return self._pick_in_other(key, fingerprint, q)
        for want in (True, False):
            for label in q["labels"]:
                if (label in chosen) != want:
                    continue
                current = self._same_question(key, fingerprint)
                option = current and next((o for o, l in zip(current["options"], current["labels"]) if l == label), None)
                if not option:
                    return False
                state = self.selected(option)
                if state is None:
                    raise RuntimeError("Cannot verify question selection state")
                if state != want:
                    self.click(option)
        current = self._same_question(key, fingerprint)
        if not current:
            return False
        picked = [bool(self.selected(o)) for o in current["options"]]
        if picked != [label in chosen for label in current["labels"]]:
            if len(chosen) > 1 and sum(picked) == 1:
                return self._pick_in_other(key, fingerprint, current)
            raise RuntimeError("Recommended selections could not be verified")
        self.click(current["submit"])
        return True

    def _pick_in_other(self, key, fingerprint, q):
        """No single recommendation: ask Claude to choose, through the Other field. On
        Claude 2.37's card the field only appears once Other is picked."""
        other = [o for o, label in zip(q["options"], q["labels"]) if label.casefold() in OTHER]
        if not q["other"] and len(other) != 1:
            raise RuntimeError("No recommended option or Other field")
        if len(other) == 1 and not self.selected(other[0]):
            self.click(other[0])   # selects Other; even on a single-choice card this does not answer yet
            q = self._same_question(key, fingerprint)
            if not q:
                return False
        if not q["other"]:
            raise RuntimeError("No recommended option or Other field")
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

    def press_card(self, key, find, card, button):
        """Press one button of a card seen this scan: `find` reads the card again
        (next_step_in / session_offer_in), `button` names the button in it ("primary",
        "start", "here"). Invoke first, a real click only when Claude ignored it, as
        approve() does. False when the card is no longer there: nothing was pressed."""
        pane = self.resolve(key, navigate=False)
        current = find(pane) if pane else None
        if not current or current["fingerprint"] != card["fingerprint"]:
            return False
        # Pressing sends the button's words as a message of yours.
        if self.value(pane.prompt):
            raise RuntimeError("Existing draft; leaving it untouched")
        if self.reveal(current[button]):
            current = self._same_card(key, find, card["fingerprint"])
            if current is None:
                return False
        if self.invoke(current[button]) and self._card_gone(key, find, card["fingerprint"]):
            return True
        again = self._same_card(key, find, card["fingerprint"])
        if again is None:
            return True
        self.click(again[button])
        if self._card_gone(key, find, card["fingerprint"]):
            return True
        raise RuntimeError("Card click was not accepted")

    @staticmethod
    def reveal(node):
        """Scroll a control into view when it lies outside the visible part of its pane:
        live on 16.10 a box at the end of the transcript sat under a narrow split's lower
        edge, where nothing can be pressed. True when it was scrolled."""
        from claude_auto_continue import auto
        try:
            if not node.control.IsOffscreen:
                return False
            pattern = node.control.GetPattern(auto.PatternId.ScrollItemPattern)
        except (AttributeError, COMError):
            return False
        if pattern is None:
            return False
        pattern.ScrollIntoView()
        time.sleep(0.3)
        return True

    def _same_card(self, key, find, fingerprint):
        pane = self.resolve(key, navigate=False)
        card = find(pane) if pane else None
        return card if card and card["fingerprint"] == fingerprint else None

    def _card_gone(self, key, find, fingerprint):
        for _ in range(3):
            time.sleep(0.4)
            if self._same_card(key, find, fingerprint) is None:
                return True
        return False

    def started_key(self, title, wait_s=NEW_TITLE_WAIT_S):
        """The sidebar key of the session a card has just started: its row is named after
        the card's title, with "Started by Claude from …" or "Started from a suggestion in
        …" after it (16.10). None when no single such row shows up in time."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
                return None
            try:
                _, entries = discover(self.snapshot())
            except RuntimeError:
                entries = []
            found = [e["key"] for e in entries if e["title"] == title and STARTED_ROW.search(e["node"].name)]
            if len(found) == 1:
                return found[0]
            time.sleep(1.0)
        return None

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
MAX_STARTS = 5                         # new sessions started from cards in one watching run


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
    wrap_up: bool = False       # this limit ended in a wrap-up, not a hard stop
    wrap_done: object = None    # reset of a wrap-up already resumed: its strip is old news
    offer_id: str = ""          # the new-session card already pressed or left alone
    box_id: str = ""            # the same for the box a turn ends with
    working: bool = False       # Stop was on screen at the last scan


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
        self.started = 0            # sessions started from cards since watching began
        self.watched = []           # the conversations of the last scan
        self.offshoots = {}         # sidebar sessions started from another: key -> (its key, status)
        self.known = None           # ...those listed when watching began, and since
        self.followed = set()       # sessions this run began to watch by itself

    def discover(self, root):
        """Open panes and sidebar entries of this app (ChatGPT's engine overrides it)."""
        return discover(root)

    def refresh(self):
        panes, entries = self.discover(self.ui.snapshot())
        self.offshoots = {}
        for entry in entries:
            parent = started_from(entry["node"])
            if parent:
                self.offshoots[entry["key"]] = (key_for(entry["kind"], parent), row_status(entry["node"]))
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
        self.started = 0
        self.known = None
        self.followed = set()

    def note(self, key, state, text, **details):
        """Log what happened to a conversation, once per change. `text` is a key or one of
        the program's own error messages; both reach the log in the window's language."""
        label = self.worker.text(text, **details)
        if state.notice != text or state.notice_label != label:
            state.notice, state.notice_label = text, label
            self.worker.log("log_chat_action", chat=key.split(":", 1)[-1], action=label)

    def logged_reset(self, now=None):
        """The reset of the newest rejected request in Claude Code's log. Given `now`, not
        one that passed over a limit window ago: that rejection belongs to an older limit.
        The log keeps rejections for days, and once the conversation that hit the limit has
        written on, an older one is the newest left in the files' tails (20.10: one from
        9.10 made every scan "already reset", and the resume went out every ten minutes)."""
        try:
            reset = self.quota.latest_reset()
        except Exception:   # an unreadable log must never stop the watch
            return None
        return None if reset and now and reset <= now - LIMIT_WINDOW else reset

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
        targets = self.watched = self.targets(panes)
        if self.follow_offshoots():
            targets = self.watched = self.targets(panes)
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
        context = observed["context"]
        if self.handover.enabled() and (not context or context.get("used") is None):
            observed["context"] = merged_context(context, self.logged_context(pane.title))
        threshold = self.worker.cfg.get("limit_threshold_pct", 100)
        meter = usage_meter(pane, parse_reset_time)
        maxed = meter["pct"] is not None and meter["pct"] >= threshold
        # The meter and the strip beside the chat box speak for the whole account: once the
        # limit runs out they show it in every conversation, also in one that had finished
        # its turn and waits for you (20.10: it got "I hit my usage limit while you were
        # working" after the reset). Only a conversation the limit stopped counts: one with
        # its own limit card or wrap-up, one working now or at the last scan, or one that
        # already waits for its reset.
        working, state.working = observed["busy"] or state.working, observed["busy"]
        stopped = (observed["card"] or observed["wrap_up"] or working
                   or (state.reason == "limit" and state.phase in ("waiting", "verifying")))
        if not stopped:
            observed["limit"], maxed = False, False
        # A wrap-up logs no rejected request: a rejection in the log belongs to an older
        # limit, and its long-past reset would resume a conversation that is still blocked.
        wrapped = observed["wrap_up"] and not observed["card"]
        logged = self.logged_reset(now) if (observed["limit"] or maxed) and not wrapped else None
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

    def answer_cards(self, key, state, observed):
        """Answer the card about a new session, or press the main button of the box a turn
        ends with, when those options are on (16.10). True when something was pressed: the
        rest is left for the next scan, which reads the window again."""
        offer, box = observed.get("offer"), observed.get("next_step")
        # Gone: the same card in a later turn is a new one.
        if not offer:
            state.offer_id = ""
        if not box:
            state.box_id = ""
        if not (offer or box):
            return False
        cfg = self.worker.cfg
        # A due handover's request is the next message; pressing too would send two.
        if (not cfg["auto_send"] or observed["limit"] or observed["error"] or observed["retry"]
                or self.handover.state_for(key).phase != "idle" or self.handover.due(key, observed)):
            return False
        # Claude's question about a new session keeps the conversation working (Stop) until
        # it is answered; a suggestion shown while it works waits for the turn to end.
        if (offer and cfg.get("auto_sessions") and offer["fingerprint"] != state.offer_id
                and (offer["kind"] == "offer" or not observed["busy"])):
            choice = "here" if cfg.get("session_choice") == "here" else "start"
            if choice == "start" and self.started >= MAX_STARTS:
                state.offer_id = offer["fingerprint"]
                self.note(key, state, "offer_left", title=offer["title"])
            else:
                if not self._press(key, state, "offer_id", session_offer_in, offer, choice):
                    return False
                if choice == "start":
                    self.started += 1
                    self.follow_started(key, self.ui.started_key(offer["title"]), offer["title"])
                self.note(key, state, "offer_" + choice, title=offer["title"])
                return True
        if box and cfg.get("auto_next_step") and not observed["busy"] and box["fingerprint"] != state.box_id:
            if box["action"]:
                state.box_id = box["fingerprint"]
                self.note(key, state, "next_step_left", label=box["label"])
                return False
            if self._press(key, state, "box_id", next_step_in, box, "primary"):
                self.note(key, state, "next_step_pressed", label=box["label"])
                return True
        return False

    def _press(self, key, state, mark, find, card, button):
        """Press a card once: `mark` (offer_id / box_id) is set before the press, so an
        uncertain one is never repeated, and cleared when nothing was pressed, so the next
        scan may try again."""
        setattr(state, mark, card["fingerprint"])
        clicks = getattr(self.ui, "clicks", None)
        try:
            pressed = self.ui.press_card(key, find, card, button)
        except Exception:
            if clicks is not None and self.ui.clicks == clicks:
                setattr(state, mark, "")
            raise
        if not pressed:
            setattr(state, mark, "")
        return pressed

    def follow_started(self, key, new_key, title):
        """Watch the session a card started, next to the conversations watched now. One that
        has not reached the sidebar in time is followed from there once it does."""
        if not new_key:
            self.worker.log("log_started_unseen", "warn", chat=key.split(":", 1)[-1], title=title)
            return
        self.watch_too(key, new_key)

    def follow_offshoots(self):
        """Watch the sessions started from a watched conversation, whoever started it:
        this program (follow_started has done it already), you, or Claude itself. One that
        shows up while watching is followed as soon as the sidebar lists it; one that was
        there before only while it still runs or waits for you, so finished ones stay out.
        Each is followed once a run: unchecking it is final. True when it followed one."""
        fresh = set() if self.known is None else self.offshoots.keys() - self.known
        self.known = (self.known or set()) | self.offshoots.keys()
        followed = False
        for key, (parent, status) in self.offshoots.items():
            if key in self.followed or key in self.watched or parent not in self.watched:
                continue
            if key in fresh or status.casefold() not in SETTLED:
                self.watch_too(parent, key)
                followed = True
        return followed

    def watch_too(self, key, new_key):
        """Watch new_key, a session started from the conversation key, next to the ones
        watched now. Watching the open ones turns into watching checked ones, as the first
        click in the list does: the new session is not open in the window, so only a check
        can watch it."""
        self.followed.add(new_key)
        cfg = self.worker.cfg
        switched = cfg.get("watch_scope") != "selected"
        chats = (list(dict.fromkeys([*self.watched, key])) if switched
                 else list(cfg.get("selected_chats", [])))
        if new_key not in chats:
            chats.append(new_key)
        cfg["watch_scope"], cfg["selected_chats"] = "selected", chats
        # The window's checks are what it sends back with every settings change: they
        # have to show the new session too, or the next change would drop it.
        self.worker.emit("selection", chats)
        chat = key.split(":", 1)[-1]
        self.worker.log("log_started_watched", chat=chat, new=new_key.split(":", 1)[-1])
        if switched:
            self.worker.log("log_watch_checked", chat=chat, count=len(chats))

    def wrapup_text(self):
        """What a conversation that wrapped up at the limit gets once the limit resets."""
        from claude_auto_continue import DEFAULT_WRAPUP_MESSAGE
        return (self.worker.cfg.get("wrapup_message") or "").strip() or DEFAULT_WRAPUP_MESSAGE

    @staticmethod
    def settle_wrap_up(state, observed, now):
        """The wrap-up strip names when the limit resets, not whether it still holds, and
        may stay on screen after the conversation went on. Once Claude has worked past
        that reset, or we resumed it, a strip with that reset no longer counts."""
        reset = observed.get("reset")
        if not observed.get("wrap_up") or observed.get("card") or not reset:
            return
        if observed.get("busy") and reset <= now and (state.wrap_done is None or reset > state.wrap_done):
            state.wrap_done = reset
        if state.wrap_done and reset <= state.wrap_done:
            observed["limit"] = observed["wrap_up"] = False

    def step(self, key, state, observed, now):
        cfg = self.worker.cfg
        self.settle_wrap_up(state, observed, now)
        if observed.get("wrap_up") or observed.get("card"):
            # How the turn ended, when the window shows it (the meter and the usage panel
            # don't): the newest reading wins, and a turn cut off after all has the card.
            state.wrap_up = not observed.get("card")
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
        if state.phase == "watching" and self.answer_cards(key, state, observed):
            return
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
                state.wrap_up = False
                self.note(key, state, "resumed")
                return
            state.phase = "watching"
        if state.phase == "watching":
            if observed["limit"]:
                state.reason, state.reset, state.since, state.clear_scans = "limit", None, now, 0
                state.wrap_up = bool(observed.get("wrap_up") and not observed.get("card"))
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
        # A conversation a handover has taken over gets nothing from here: it was handed on,
        # its new conversation is being opened, or a person has to look.
        if self.handover.holds(key, state):
            return
        # After a wrap-up with the context over the threshold the work moves to a new
        # conversation built from the wrap-up's own summary: nothing goes to this one.
        if state.reason == "limit" and state.wrap_up and self.handover.from_wrap_up(key, state, observed, now):
            return
        state.attempts += 1
        state.phase = "verifying"
        state.due = now + dt.timedelta(seconds=cfg.get("verify_delay_s", 30))
        # With the context over the threshold, the message that resumes a blocked chat is
        # the request for a handover, not the user's text (and never the Try again button).
        # A chat that wrapped up by itself is pointed at what it listed as left.
        handover = self.handover.resume_text(key, state, observed, now)
        wrapped = state.reason == "limit" and state.wrap_up and not handover
        message = handover or (self.wrapup_text() if wrapped else None)
        try:
            result = self.ui.resume(key, cfg.get("prefer_try_again", False) and not message,
                                    state.reason == "api", message=message)
        except Exception:
            state.phase = "waiting"
            state.due = state.not_before = now + dt.timedelta(seconds=max(30, cfg.get("api_retry_wait_s", 30)))
            raise
        if handover:
            self.handover.resume_sent(key, now)
        if state.reason == "limit":
            # If the chat is still blocked afterwards, a stale reset must not
            # trigger another message on the very next scan.
            state.not_before = now + dt.timedelta(seconds=cfg["retry_wait_s"])
        if wrapped and result == "message":
            state.wrap_done, result = state.reset or now, "wrapup_message"
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
            self.note(key, state, "waiting_wrapup_at" if state.wrap_up else "waiting_limit_at",
                      reset=f"{state.reset:%H:%M}", send=f"{state.due:%H:%M:%S}")
            return now >= state.due
        state.since = state.since or now
        cap = state.since + LIMIT_WINDOW + delay
        state.due = max(cap, state.not_before or cap)
        self.note(key, state, "waiting_limit_unknown", send=f"{state.due:%H:%M:%S}")
        state.clear_scans = 0 if observed["limit"] else state.clear_scans + 1
        gone = state.clear_scans >= CLEAR_SCANS and now >= (state.not_before or now)
        return gone or now >= state.due
