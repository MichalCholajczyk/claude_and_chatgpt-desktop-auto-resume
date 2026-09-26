"""ChatGPT desktop app (Codex mode) adapter and scheduler for Auto-Resume.

Reads the ChatGPT window through UI Automation the same way session_automation
reads Claude: no coordinates or UIA objects survive a scan, every action resolves
its conversation again, and ambiguous titles, drafts and unknown controls fail
closed. The per-conversation scheduler (SessionEngine.step) is shared with Claude;
what differs is how a conversation, its composer, the limit notice and the reset
time are found.

Layout of the app, as exposed to UI Automation (English / Polish labels):
  sidebar   "Switch mode, current mode: Codex"; chat rows are buttons named after
            the chat, each holding "Pin chat" and "Archive chat" buttons; at the
            bottom "Open profile menu" (the user button)
  main      (landmark) header button named after the open chat; the transcript
            ("You said:" / "ChatGPT said:" headings); the composer: a text box next
            to "Add files and more" and a submit button ("Send", "Stop" while working)
  turn      after the last role heading, in document order: paragraphs, tool
            summaries, the notice, then cards ("Review changed files", one button
            per file, "Show 1 more file"), the message actions and a time stamp
            ("10 wrz, 4:43"). How they nest differs between versions: 26.917 lists
            them as the heading's siblings, the newer layout (workedFor.v2) groups
            the agent's activity, a divider "Worked for 32m" / "Przetwarzano przez …"
            and the final response in containers of their own
  notice    "You've hit your usage limit. ... or try again at 9:33 AM." ends a
            conversation stopped by the limit (the time only while it is active)
  card      while the limit lasts, an account-wide card sits above the composer:
            "You're out of Codex and Work usage" ... with "Upgrade" and "Reset
            usage" (spends one of the user's resets: never clicked)
  menu      profile menu -> "Usage remaining" expands rows: "5h" "0%" "9:33 AM"
"""
import datetime as dt
import os
import re
import time

from chatgpt_limits import blocked_until, is_notice, notice_reset, summarize
from codex_log import CodexLog
from input_lock import input_lock, worker_cancelled
from handover import HandoverRefused
from session_automation import ClaudeUI, Pane, SessionEngine, key_for
from strings import Text

MODE_TRIGGER = re.compile(r"^(?:Switch mode, current mode|Zmień tryb, bieżący tryb):\s*(?P<mode>.+?)\s*$", re.I)
ROW_ACTIONS = {"archive chat", "archiwizuj czat", "pin chat", "przypnij czat", "unpin chat", "odepnij czat"}
CONTEXT_BUTTON = {"add files and more", "dodaj pliki i nie tylko"}
SEND = {"send", "wyślij"}
BUSY_SUBMIT = {"stop", "zatrzymaj", "queue", "kolejkuj", "steer", "wykonaj"}   # a run is in progress
STOP = {"stop", "zatrzymaj"}          # ...and this one interrupts it
NEW_CHAT_LABELS = {"new chat", "nowy czat", "new conversation", "nowa rozmowa"}
# The new-conversation screen names its project on a button of its own.
PROJECT_BUTTON = re.compile(r"^(?:Zmień projekt|Change project)\s*:\s*(?P<project>.+)$", re.I)
RESUME_SUBMIT = {"resume", "wznów"}
VOICE = {"start voice chat", "rozpocznij czat głosowy"}                      # shown instead of Send when empty
IN_CALL = {"end voice chat", "zakończ czat głosowy"}                        # a voice chat is running
SUBMIT = SEND | BUSY_SUBMIT | RESUME_SUBMIT | VOICE | IN_CALL
USER_HEADING = re.compile(r"^(?:You said|Twoja wiadomość):$", re.I)
ROLE_HEADING = re.compile(r"^(?:You said|Twoja wiadomość|[\w .\-]{1,40} (?:said|powiedział|powiedziała)):$", re.I)
RETRY = re.compile(r"^(?:Retry|Ponów)(?:\s+(?:in|za)\s+\d+\s*s)?$", re.I)
RETRY_COUNTDOWN = re.compile(r"^(?:Retry in|Ponów za)\s+\d+\s*s$", re.I)   # ChatGPT retries by itself
# Codex asks mid-run questions in a panel with "Skip" and a free-text answer; it
# dismisses them on its own after a countdown, so a chat with one is simply busy.
QUESTION_FIELDS = {"or write your own response", "lub napisz własną odpowiedź", "reply…", "odpowiedz…"}
QUESTION_SKIP = re.compile(r"^(?:Skip|Pomiń)(?:,.*)?$", re.I)
WORKING = re.compile(r"^(?:Working…|Pracuję…|Reconnecting\b|Ponowne łączenie\b|Reconnecting to ChatGPT|"
                     r"Server is busy, reconnecting|Serwer jest zajęty|"
                     r"Working(?: for\s.*)?$|Pracuje(?: od\s.*)?$)", re.I)   # the newer layout's divider
API_ERROR = re.compile(
    r"^(?:stream disconnected|unexpected status \d{3}|error sending request|request failed|"
    r"An error occurred|Something went wrong|Coś poszło nie tak|Wystąpił błąd|Podczas tego czatu wystąpił błąd|"
    r"Internal server error|Service unavailable|Bad gateway|Gateway timeout|Too many requests|"
    r"(?:Our |The )?servers? (?:is|are) (?:currently )?(?:overloaded|busy|at capacity)|"
    r"Serwer(?:y)? (?:jest|są) (?:przeciążon\w*|zajęt\w*)|We(?:'|’)re currently experiencing high demand)", re.I)
PERMANENT_ERROR = re.compile(r"\b(?:401|403|unauthorized|forbidden|sign in again|log in again|"
                             r"zaloguj się ponownie|invalid api key|account (?:is )?(?:deactivated|suspended))\b", re.I)
# Labels that are not messages: a time stamp ("05:49", "wtorek, 05:49", "10 wrz, 4:43")
# or a turn's duration divider ("Worked for 34m", "Pracował przez 34 min", "Przetwarzano
# przez 32 min 37 s", "Working for 2m" / "Pracuje od 2 min", "You stopped after …" / "Przerwano po …").
TRAILING_NOISE = re.compile(r"^(?:[\w.]+,?\s+){0,3}\d{1,2}:\d{2}(?:\s*[AP]\.?M\.?)?$|"
                            r"^(?:Worked for|Pracował przez|Przetwarzano przez|Working for|Pracuje od|"
                            r"You stopped after|Przerwano po)\s", re.I)
INTERACTIVE = {"ButtonControl", "HyperlinkControl", "MenuItemControl", "CheckBoxControl",
               "RadioButtonControl", "TabItemControl", "ComboBoxControl"}
MAX_NOTICE_CHARS = 400      # the notices are one or two sentences; prose is longer
HEADER_BAND = 90            # px below the top of the main area that belong to its header
USAGE_CHECK_S = 600         # default spacing of the "Usage remaining" check while watching
USAGE_FRESH_S = 60          # a menu reading this recent is reused instead of opening it again


# ------------------------------------------------------------------ discovery

def current_mode(nodes):
    """"codex" or "chat": which list the sidebar shows (chat keys include it)."""
    for n in nodes:
        if n.type == "ButtonControl":
            m = MODE_TRIGGER.match(n.name.strip())
            if m:
                return m["mode"].casefold()
    return "codex"


def document_title(nodes):
    """The page title, which is the open chat's title."""
    docs = [n for n in nodes if n.type == "DocumentControl" and n.visible]
    return docs[0].name.strip() if len(docs) == 1 else ""


def composer_box(edit):
    """The composer around a text box: the nearest ancestor that also holds the
    "Add files and more" button. (Its submit button changes: "Send" with text, "Start
    voice chat" when empty, "Stop" while working.) None for other text boxes."""
    node = edit.parent
    for _ in range(4):
        if node is None:
            return None
        if any(n.type == "ButtonControl" and n.name.strip().casefold() in CONTEXT_BUTTON for n in node.walk()):
            return node
        node = node.parent
    return None


def discover(root):
    """Return the open conversation (as a one-item pane list) and the sidebar chats.
    Duplicate titles stay in the list; acting on them is refused as ambiguous."""
    nodes = list(root.walk())
    kind = current_mode(nodes)
    entries = []
    for row in nodes:
        if row.type != "ButtonControl" or not row.name.strip() or row.name.strip().casefold() in ROW_ACTIONS:
            continue
        if any(a.role == "main" for a in row.ancestors()):
            continue
        if any(n is not row and n.type == "ButtonControl" and n.name.strip().casefold() in ROW_ACTIONS
               for n in row.walk()):
            title = row.name.strip()
            entries.append(dict(key=key_for(kind, title), title=title, kind=kind, node=row, source="sidebar"))
    panes = []
    main = [n for n in nodes if n.role == "main"]
    title = document_title(nodes)
    if len(main) == 1 and title:
        area = main[0]
        # The chat's own title button sits in the header band at the top of the main area.
        headers = [n for n in area.walk() if n.type == "ButtonControl" and n.name.strip() == title
                   and n.visible and n.rect[1] < area.rect[1] + HEADER_BAND]
        composers = [n for n in area.walk() if n.type == "EditControl" and n.visible and composer_box(n)]
        if headers and len(composers) == 1:
            panes.append(Pane(key_for(kind, title), title, kind, area, composers[0]))
    return panes, entries


# -------------------------------------------------------------------- signals

def transcript(pane):
    """Conversation nodes in reading order: after the header's title button,
    before the composer."""
    box = composer_box(pane.prompt)
    nodes, started = [], False
    for n in pane.root.walk():
        if n is box:
            break
        if started:
            nodes.append(n)
        elif n.type == "ButtonControl" and n.name.strip() == pane.title:
            started = True
    return nodes


def quoted(node):
    """Text inside code, quotes or text boxes is content, never a live notice."""
    return any(a.type in ("CodeControl", "EditControl") or a.role in ("code", "blockquote")
               for a in node.ancestors())


def question_open(pane):
    return any((n.type == "EditControl" and n.name.strip().casefold() in QUESTION_FIELDS) or
               (n.type == "ButtonControl" and QUESTION_SKIP.match(n.name.strip()))
               for n in pane.root.walk())


def _covers(outer, inner, slack=2):
    return (inner[0] <= outer[0] + slack and inner[1] <= outer[1] + slack and
            inner[2] >= outer[2] - slack and inner[3] >= outer[3] - slack)


def is_role_heading(n):
    return n.type == "TextControl" and n.role == "heading" and ROLE_HEADING.match(n.name.strip())


def composer_area(pane, headings):
    """Everything around the composer that holds no message: the highest ancestor of
    its box without a role heading (the limit card above the composer sits there)."""
    box = composer_box(pane.prompt)
    if box is None or not headings:
        return set()
    marked = {a for h in headings for a in h.ancestors()}
    area = box
    while area.parent is not None and area.parent is not pane.root and area.parent not in marked:
        area = area.parent
    return set(area.walk())


def in_control(node, stop):
    """A label of a button, a link or another control, never message text."""
    for a in node.ancestors():
        if a is stop:
            return False
        if a.type in INTERACTIVE:
            return True
    return False


def in_card(node, stop, memo):
    """Text of a clickable card, such as the changed-files summary: the nearest
    element around it that holds any control is covered by one of its controls.
    (A link or a Retry button inside a paragraph covers nothing.)"""
    for a in node.ancestors():
        if a is stop:
            return False
        if a not in memo:
            controls = [n for n in a.walk() if n is not a and n.type in INTERACTIVE]
            memo[a] = any(_covers(a.rect, c.rect) for c in controls) if controls else None
        if memo[a] is not None:
            return memo[a]
    return False


def notice_pieces(notice):
    """The notice's own element when it holds nothing but a short text — its time may
    be a separate piece — else the notice text alone."""
    row = notice.parent
    if row is not None and not any(n.type in INTERACTIVE or is_role_heading(n) for n in row.walk()):
        pieces = [n for n in row.walk() if n.type == "TextControl" and n.name.strip()]
        if len("".join(n.name for n in pieces)) <= MAX_NOTICE_CHARS:
            return pieces
    return [notice]


def last_turn(pane):
    """(texts, words, nodes, heading) of the conversation after its last role heading,
    in document order — however the version nests them. `texts`: every text, for the
    busy check; `words`: the message texts among them, without control labels, cards,
    the area around the composer, time stamps and duration dividers; `heading`: the role
    heading itself, which says whose turn it is."""
    nodes = transcript(pane)
    headings = [i for i, n in enumerate(nodes) if is_role_heading(n)]
    texts = [n for n in nodes[headings[-1] + 1 if headings else 0:]
             if n.type == "TextControl" and n.name.strip() and n.role != "heading" and not quoted(n)]
    area, memo = composer_area(pane, [nodes[i] for i in headings]), {}
    words = [n for n in texts if n not in area and not in_control(n, pane.root)
             and not in_card(n, pane.root, memo) and not TRAILING_NOISE.match(n.name.strip())]
    return (texts, words, nodes[headings[-1] + 1 if headings else 0:],
            nodes[headings[-1]] if headings else None)


def signals(pane, now=None, blocked=False):
    """What the conversation shows now. Only the last turn counts: after a resume the
    old notice stays in the history, followed by the new message. The notice has to
    be the last message text (its own pieces aside). With `blocked` (the menu shows a
    used-up window) any notice in the last turn is enough; without it, a notice that
    something unknown follows is a `candidate` for the menu to settle."""
    now = now or dt.datetime.now()
    texts, words, after, heading = last_turn(pane)
    notices = [n for n in words if is_notice(n.name) and len(n.name.strip()) <= MAX_NOTICE_CHARS]
    notice = None
    if notices:
        pieces = notice_pieces(notices[-1])
        if blocked or all(n in pieces for n in words[words.index(notices[-1]) + 1:]):
            notice = " ".join("".join(n.name for n in pieces).split())
    limit = notice is not None
    last = words[-1].name.strip() if words else ""
    error = last if not limit and len(last) <= MAX_NOTICE_CHARS and API_ERROR.match(last) else None
    buttons = [n for n in after if n.type == "ButtonControl" and n.visible]
    retries = [b for b in buttons if RETRY.match(b.name.strip())]
    countdown = any(RETRY_COUNTDOWN.match(b.name.strip()) for b in retries)
    box = composer_box(pane.prompt)
    submit = [n.name.strip().casefold() for n in (box.walk() if box else ())
              if n.type == "ButtonControl" and n.name.strip().casefold() in SUBMIT]
    busy = (any(name in BUSY_SUBMIT | IN_CALL for name in submit) or countdown or question_open(pane)
            or any(WORKING.match(n.name.strip()) for n in texts[-3:] + words[-3:]))
    # The handover marker is looked for in the last reply. Our own request quotes the
    # marker's shape, so a turn of ours must never count as an answer. The context of a
    # Codex conversation comes from its log, which the engine fills in.
    ours = heading is not None and USER_HEADING.match(heading.name.strip())
    return dict(limit=limit, reset=notice_reset(notice, now) if limit else None, error=error,
                permanent=bool(error and PERMANENT_ERROR.search(error)),
                retry=retries[0] if len(retries) == 1 and not countdown else None,
                busy=busy, question=None, candidate=bool(notices) and not limit, last=last,
                context=None,
                last_text="" if ours else " ".join(n.name.strip() for n in words if n.name.strip()))


# -------------------------------------------------------------- UI automation

class ChatGPTUI(ClaudeUI):
    """Clicks and types in ChatGPT; snapshot, click and type_into are shared with Claude."""

    def resolve(self, key, navigate=True):
        root = self.snapshot()
        panes, entries = discover(root)
        matches = [p for p in panes if p.key == key]
        if len(matches) == 1:
            return matches[0]
        if matches or not navigate:
            return None
        rows = [e for e in entries if e["key"] == key]
        if len(rows) != 1:
            return None     # missing, in the other mode, or ambiguous: never guess
        self.click(rows[0]["node"])
        # Opening a chat is asynchronous: confirm its own title and composer.
        for _ in range(6):
            time.sleep(0.4)
            panes, _ = discover(self.snapshot())
            matches = [p for p in panes if p.key == key]
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def value(node):
        try:
            value = node.control.GetValuePattern().Value
        except Exception:
            try:
                value = node.control.GetTextPattern().DocumentRange.GetText(-1)
            except Exception:
                raise RuntimeError("Cannot verify composer contents")
        value = (value or "").strip()
        if not value or value != node.name.strip():
            return value
        # An empty composer reports its placeholder, which is also its name: an empty
        # paragraph ("\n") plus the placeholder text in a group of its own. A typed
        # draft with the same words is a single paragraph.
        leaves = [n for n in node.walk() if n is not node and n.type == "TextControl"]
        placeholder = [n for n in leaves if n.name.strip() == value]
        empty_paragraph = any(not n.name.strip() for n in leaves)
        if not placeholder or (empty_paragraph and all(n.parent is not e.parent for n in placeholder
                                                         for e in leaves if not e.name.strip())):
            return ""
        return value

    def panes(self, root=None):
        return discover(root if root is not None else self.snapshot())[0]

    def send(self, pane, message):
        """Type and submit, then make sure the message really left the composer. A new
        conversation has no key yet, so there is nothing to resolve it by: the caller
        confirms it by waiting for the conversation to appear."""
        text = super().send(pane, message)
        if pane.key:
            self.confirm_sent(pane.key, text)
        return text

    def stop(self, pane):
        """ChatGPT's submit button reads "Stop" while a run is in progress."""
        box = composer_box(pane.prompt)
        buttons = [n for n in (box.walk() if box else ()) if n.type == "ButtonControl"
                   and n.name.strip().casefold() in STOP and n.visible]
        if len(buttons) != 1:
            return False
        self.click(buttons[0])
        return True

    def resume(self, key, prefer_retry=False, api_error=False, message=None):
        from claude_auto_continue import DEFAULT_MESSAGE
        pane = self.resolve(key)
        if pane is None:
            raise RuntimeError("Selected conversation is missing or ambiguous")
        state = signals(pane)
        if state["busy"]:
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

    def new_session_screen(self, root):
        """(prompt, project button) of ChatGPT's new-conversation screen.

        It has no title, so `discover` sees no pane. The project sits on a button whose
        name carries it: "Zmień projekt: auto-resume" / "Change project: auto-resume".
        """
        taken = {pane.prompt for pane in self.panes(root)}
        prompts = [n for n in root.walk() if n.type == "EditControl" and n.visible
                   and composer_box(n) and n not in taken]
        if len(prompts) != 1:
            return None, None
        buttons = [n for n in root.walk() if n.type == "ButtonControl" and n.visible
                   and PROJECT_BUTTON.match(n.name.strip())]
        return prompts[0], (buttons[0] if len(buttons) == 1 else None)

    def project_name(self, button):
        match = PROJECT_BUTTON.match(button.name.strip())
        return (match["project"].strip() if match else "")

    def new_chat(self, key, project_root, message):
        """Start a conversation in the same project as `key` and send it `message`."""
        wanted = os.path.basename(os.path.normpath(project_root or "")).casefold()
        if not wanted:
            raise HandoverRefused("The handover names no project folder")
        pane = self.resolve(key)
        if pane is None:
            raise HandoverRefused("The conversation the handover came from is missing")
        if self.value(pane.prompt):
            raise RuntimeError("Existing draft; leaving it untouched")
        root = self.snapshot()
        before = {p.key for p in self.panes(root)}
        buttons = [n for n in root.walk() if n.type == "ButtonControl" and n.visible
                   and n.name.strip().casefold() in NEW_CHAT_LABELS]
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
        if self.project_name(project).casefold() != wanted:
            if not self.pick_project(project, wanted):
                raise HandoverRefused("The project of the handover is not on the list")
            prompt, project = self.new_session_screen(self.snapshot())
            if prompt is None or project is None or self.project_name(project).casefold() != wanted:
                raise HandoverRefused("The new conversation is not in the project of the handover")
        self.send(Pane("", "", "", root, prompt), message)
        return self.settled_key(before)

    def confirm_sent(self, key, message):
        """Enter sends by default; with "Ctrl+Enter to send" it only adds a line, so
        fall back to the Send button of the same, verified composer. While the limit
        lasts ChatGPT keeps Send disabled: the message is then taken back, since a
        draft left in the composer would block every later resume."""
        for attempt in range(2):
            for _ in range(12):
                time.sleep(0.25)
                pane = self.resolve(key, navigate=False)
                if pane is None or not self.value(pane.prompt).startswith(message):
                    return
            if attempt:
                break
            box = composer_box(pane.prompt)
            send = [n for n in (box.walk() if box else ()) if n.type == "ButtonControl"
                    and n.name.strip().casefold() in SEND and n.visible]
            if len(send) != 1:
                break
            if self.enabled(send[0]) is False:
                if self.withdraw(pane.prompt, message):
                    raise RuntimeError("ChatGPT did not accept the message (the limit may still be on); "
                                       "it was removed from the composer")
                raise RuntimeError("ChatGPT did not accept the message (the limit may still be on); "
                                   "it is left in the composer")
            self.click(send[0])
        pane = self.resolve(key, navigate=False)
        if pane is not None and self.withdraw(pane.prompt, message):
            raise RuntimeError("Message was typed but not sent; it was removed from the composer")
        raise RuntimeError("Message was typed but not sent; it is left in the composer")

    def withdraw(self, node, message):
        """Empty the composer again, but only while it holds exactly the message typed
        a moment ago (the composer was verified empty before typing). True when empty."""
        from claude_auto_continue import auto, user32

        def holds():
            try:
                return self.value(node)
            except RuntimeError:
                return None
        if holds() != message:
            return False
        try:
            pattern = node.control.GetValuePattern()
            if pattern and not pattern.IsReadOnly:
                pattern.SetValue("")
        except Exception:
            pass
        if holds() == message and user32.GetForegroundWindow() == self.worker.hwnd:
            try:
                focused = node.control.HasKeyboardFocus
            except Exception:
                focused = False
            if focused:     # select-all + delete only inside the verified, focused composer
                self.press("{Ctrl}a{Delete}")
        return holds() == ""

    def answer(self, key, fingerprint):
        return False    # Codex's mid-run questions skip themselves after a countdown


# ------------------------------------------------------------------ scheduler

class ChatGPTEngine(SessionEngine):
    """Shared scheduler; the reset comes from the physical check of "Usage remaining",
    which is also repeated every usage_check_s while watching to show what is left."""

    def __init__(self, worker, ui=None, quota=None):
        super().__init__(worker, ui or ChatGPTUI(worker), quota or CodexLog())
        self.cleared = {}         # key -> when the menu first showed the limit over (fixed send time)
        self.usage_rows = None    # rows of the latest successful menu reading
        self.usage_tried = None   # monotonic time of the latest attempt to read the menu
        self.usage_failed = False # the latest attempt failed (logged once per streak)
        self.usage_shown = None   # what the log last reported as left
        self.candidate_due = {}   # key -> monotonic time when a notice candidate may open the menu again
        self.no_chat_logged = False

    def discover(self, root):
        return discover(root)

    def reset(self):
        super().reset()
        self.usage_tried = None   # a new watch checks the menu on its first scan
        self.candidate_due.clear()
        self.no_chat_logged = False

    def targets(self, panes):
        """While watching the open conversation, say once when none can be found, so a
        view the tool does not understand shows up in the log instead of silence."""
        found = super().targets(panes)
        if self.worker.cfg.get("watch_scope", "open") == "open":
            if not found and not self.no_chat_logged:
                self.worker.log("log_no_open_chat", "warn")
            self.no_chat_logged = not found
        return found

    def tick(self, now=None):
        now = now or dt.datetime.now()
        scanning = time.monotonic() >= self.next_scan
        super().tick(now)
        # Only after a scan that could read the window: nothing is clicked in a hidden
        # one, and nothing at all while someone else is using the mouse.
        if scanning and not self.last_scan_error and not self.worker.pause_reason:
            self.check_usage()

    def check_usage(self):
        """Read "Usage remaining" when the last reading is usage_check_s old (0 = only
        when a conversation needs it), so the window shows what is left."""
        interval = self.worker.cfg.get("usage_check_s", USAGE_CHECK_S)
        if interval <= 0 or (self.usage_tried is not None and time.monotonic() < self.usage_tried + interval):
            return
        try:
            with input_lock(worker_cancelled(self.worker)):
                self.read_usage()
        except Exception as exc:
            self.worker.log("log_monitor_error", "warn", err=self.worker.text(str(exc)))

    def read_usage(self, session=None):
        """Rows of "Usage remaining", or None when unreadable. A reading from the last
        minute is reused instead of opening the menu again."""
        if self.usage_tried is not None and time.monotonic() - self.usage_tried < USAGE_FRESH_S:
            return None if self.usage_failed else self.usage_rows
        self.usage_tried = time.monotonic()
        rows, ok = self.worker._read_usage_menu(self.worker._get_window())
        if not ok:
            if not self.usage_failed:
                self.worker.log("log_panel_unreadable", "warn")
            self.usage_failed = True
            return None
        self.usage_rows, self.usage_failed = rows, False
        self.worker.emit("usage", dict(rows=summarize(rows), **({"session": session} if session else {})))
        shown = Text.joined(", ", [self.worker.text("usage_row", window=self.window_name(r), left=r["left"])
                                   for r in rows if r["model"] is None and r["label"]])
        if shown and shown != self.usage_shown:
            self.usage_shown = str(shown)
            self.worker.log("log_usage_left", rows=shown)
        return rows

    WINDOWS = {300: "window_5h", 1440: "window_daily", 10080: "window_weekly", 43200: "window_monthly"}

    def window_name(self, row):
        """Our own name of a limit window ("5-hour", "weekly"): ChatGPT's label follows
        ChatGPT's language ("5 godz.", "Co tydzień"), the log follows this program's."""
        minutes = row["minutes"]
        if minutes in self.WINDOWS:
            return self.worker.text(self.WINDOWS[minutes])
        if minutes and minutes < 1440 and minutes % 60 == 0:
            return self.worker.text("window_hours", n=minutes // 60)
        return self.worker.text("window_raw", label=row["label"])

    def account_blocked(self, now):
        """The latest reading shows a used-up window that has not reset yet."""
        if not self.usage_rows:
            return False
        blocked, until = blocked_until(self.usage_rows)
        return blocked and (until is None or until > now)

    def scan_one(self, key, state, now):
        pane = self.ui.resolve(key, navigate=self.worker.cfg.get("watch_scope") == "selected")
        if pane is None:
            self.note(key, state, "unavailable")
            return
        observed = signals(pane, now, blocked=self.account_blocked(now))
        if observed["candidate"] and time.monotonic() >= self.candidate_due.get(key, 0):
            # A stop notice that something unknown follows: "Usage remaining" settles it.
            self.candidate_due[key] = time.monotonic() + self.worker.cfg.get("panel_backoff_s", 300)
            self.read_usage(session=pane.title)
            if self.account_blocked(now):
                observed = signals(pane, now, blocked=True)
        self.worker.emit("status", "ok")
        if observed["limit"]:
            observed["reset"] = self.limit_reset(key, state, observed, pane, now)
        else:
            self.cleared.pop(key, None)
            if self.account_blocked(now) and not observed["busy"]:
                self.note(key, state, "blocked_no_notice", seen=" ".join(observed["last"].split())[:80] or "—")
        # Codex's window shows no context reading, but its own log does.
        observed["context"] = self.logged_context(pane.title)
        self.step(key, state, observed, now)
        self.after_observe(key, state, observed, now, pane)

    def limit_reset(self, key, state, observed, pane, now):
        """When the limit that stopped this conversation is over.

        First the physical check: open the profile menu, expand "Usage remaining"
        and read which windows are used up (0% left) and when they reset. If none
        is used up, the limit is already over and the conversation is resumed after
        the usual delay. Without the menu: the time in the notice (shown only while
        the limit is active) or in Codex's log, if still ahead."""
        unknown = state.phase == "waiting" and state.reason == "limit" and state.reset is None
        rows = None
        if (state.phase == "watching" or unknown) and time.monotonic() >= state.next_panel:
            state.next_panel = time.monotonic() + self.worker.cfg.get("panel_backoff_s", 300)
            rows = self.read_usage(session=pane.title)
        if rows is not None:
            blocked, until = blocked_until(rows)
            if not blocked:
                return self.cleared.setdefault(key, now)
            self.cleared.pop(key, None)
            if until:
                return until
        elif key in self.cleared:
            return self.cleared[key]
        ahead = [r for r in (observed["reset"], self.logged_reset()) if r and r > now]
        return max(ahead, default=None)
