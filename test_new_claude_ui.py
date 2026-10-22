"""Claude Desktop 2.37 (measured live on 15.10): what changed in the window and what the
program has to read differently. Every tree here copies a measured one; nothing touches a
real window."""
import _ctypes
import datetime as dt
import importlib.util
import json
import os
import queue
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import claude_auto_continue as app
from quota_log import QuotaLog
from session_automation import (ClaudeUI, SessionEngine, SessionState, WindowHidden, allow_name, discover,
                                final_limit_card, last_reply_text, permission_in, question_in, signals)
from test_sessions import LIMIT_CARD, FakeQuota, message, node, pane
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


def toggle(n, state=0):
    """A button with aria-pressed, as the options of the 2.37 question card."""
    pattern = types.SimpleNamespace(ToggleState=state)
    n.control = types.SimpleNamespace(GetTogglePattern=lambda: pattern, IsEnabled=True)
    return pattern


def selected_radio(n, on):
    n.control = types.SimpleNamespace(GetSelectionItemPattern=lambda: types.SimpleNamespace(IsSelected=on))
    return n


def worker():
    w = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
    w.log = lambda *a, **kw: None
    return w


# ------------------------------------------------------------------ reading the window

class FakeControl(types.SimpleNamespace):
    def __init__(self, name, kind, rect=(0, 0, 100, 100)):
        super().__init__(Name=name, ControlTypeName=kind, AriaRole="",
                         BoundingRectangle=types.SimpleNamespace(left=rect[0], top=rect[1],
                                                                 right=rect[2], bottom=rect[3]))


class WindowReadingTests(unittest.TestCase):
    def setUp(self):
        self.worker = worker()
        self.worker._get_window = lambda: types.SimpleNamespace(NativeWindowHandle=5)
        self.worker._wake_accessibility = lambda win: None
        self.ui = ClaudeUI(self.worker)

    def walk(self, *controls, fail_after=None):
        def walk(win, includeTop=False, maxDepth=130):
            for i, (ctrl, depth) in enumerate(controls):
                if fail_after is not None and i == fail_after:
                    raise _ctypes.COMError(-2147220991, "Event cannot call any subscribers", (None, None, None, 0, None))
                yield ctrl, depth
        return patch.object(app.auto, "WalkControl", walk)

    FRAME = ((FakeControl("Claude", "PaneControl"), 1), (FakeControl("Minimalizuj", "ButtonControl"), 2),
             (FakeControl("Chrome Legacy Window", "PaneControl"), 2))

    def test_a_window_that_shows_no_page_is_not_read_as_empty(self):
        # 15.10, right after Claude 2.37 started: only the frame, no document at all.
        with self.walk(*self.FRAME):
            with self.assertRaisesRegex(RuntimeError, "Accessibility scan incomplete"):
                self.ui.snapshot()

    def test_a_page_is_read(self):
        page = (FakeControl("Obsługa - Claude Code", "DocumentControl", (0, 0, 900, 900)), 3)
        with self.walk(*self.FRAME, page, (FakeControl("New", "ButtonControl"), 4)):
            root = self.ui.snapshot()
        self.assertIn("New", [n.name for n in root.walk()])

    def test_a_tree_rebuilt_during_the_walk_is_read_again_later(self):
        # 15.10: opening a conversation from the sidebar, the walk failed with COM's
        # "Event cannot call any subscribers".
        page = (FakeControl("Claude", "DocumentControl", (0, 0, 900, 900)), 3)
        with self.walk(*self.FRAME, page, (FakeControl("New", "ButtonControl"), 4), fail_after=4):
            with self.assertRaisesRegex(RuntimeError, "Accessibility tree changed"):
                self.ui.snapshot()

    def test_opening_a_chat_waits_out_a_tree_being_rebuilt(self):
        old, new = pane("Alpha"), pane("Beta")
        sidebar = node("Sidebar", parent=old.root)
        row = node(parent=sidebar)
        node("Beta", "ButtonControl", row)
        node("More options for Beta", "ButtonControl", row)
        reads = iter([old.root, RuntimeError("Accessibility tree changed; retry on next scan"), new.root])

        def snapshot():
            item = next(reads)
            if isinstance(item, Exception):
                raise item
            return item
        self.ui.snapshot = snapshot
        self.ui.click = lambda n: None
        with patch("session_automation.time.sleep", lambda s: None):
            found = self.ui.resolve("code:Beta")
        self.assertEqual(found.key, "code:Beta")

    def reads(self, *walks):
        """WalkControl giving each read its own controls, one read after another."""
        walks = iter(walks)

        def walk(win, includeTop=False, maxDepth=130):
            yield from next(walks)
        return patch.object(app.auto, "WalkControl", walk)

    EMPTY_PAGE = ((FakeControl("ChatGPT", "DocumentControl", (0, 0, 0, 0)), 3),)

    def test_a_page_still_being_built_is_read_on_a_second_look(self):
        # 15.10: the first read of a ChatGPT window nobody had read for a while found an
        # empty page; a moment later the same window read fine.
        page = ((FakeControl("ChatGPT", "DocumentControl", (0, 0, 900, 900)), 3),
                (FakeControl("New chat", "ButtonControl"), 4))
        woken = []
        self.worker._wake_accessibility = lambda win: woken.append(win)
        with self.reads(self.FRAME + self.EMPTY_PAGE, self.FRAME + page), \
                patch("session_automation.time.sleep", lambda s: None):
            root = self.ui.snapshot()
        self.assertIn("New chat", [n.name for n in root.walk()])
        self.assertEqual(len(woken), 2)

    def test_a_page_empty_on_both_looks_is_a_hidden_window(self):
        with self.reads(self.FRAME + self.EMPTY_PAGE, self.FRAME + self.EMPTY_PAGE), \
                patch("session_automation.time.sleep", lambda s: None):
            with self.assertRaises(WindowHidden):
                self.ui.snapshot()

    def test_the_render_widget_is_asked_for_the_page(self):
        asked = []
        with patch.object(app, "render_widgets", lambda hwnd: [67690]), \
                patch.object(app.auto, "ControlFromHandle", lambda h: asked.append(h) or Mock()), \
                patch.object(app.auto, "WalkControl", lambda *a, **kw: iter(())):
            app.MonitorWorker._wake_accessibility(types.SimpleNamespace(NativeWindowHandle=67634))
        self.assertEqual(asked, [67690])

    def test_render_widgets_are_found_among_the_child_windows(self):
        classes = {11: "Intermediate D3D Window", 12: "Chrome_RenderWidgetHostHWND"}

        def enum(hwnd, callback, _lparam):
            for child in classes:
                callback(child, 0)
            return True

        def class_name(hwnd, buf, size):
            buf.value = classes[hwnd]
            return len(buf.value)
        fake = types.SimpleNamespace(EnumChildWindows=enum, GetClassNameW=class_name)
        with patch.object(app, "user32", fake):
            self.assertEqual(app.render_widgets(5), [12])


# ------------------------------------------------------------------ sidebar and mode

def sidebar_row(sidebar, title, shown=None):
    """A 2.37 row: a button named after the chat (plus a subtitle, sometimes), its
    texts, and the "More options for …" button beside it."""
    row = node(parent=sidebar)
    node(shown or title, "ButtonControl", row)
    node(title, "TextControl", row)
    node("More options for " + title, "ButtonControl", row)
    return row


def mode_switch(root, selected):
    bar = node("Sidebar", parent=root)
    group = node("Mode", parent=bar)
    claude = selected_radio(node("Claude" + (", working" if selected == "Code" else ""), "RadioButtonControl", group),
                            selected == "Claude")
    code = selected_radio(node("Code" + (", working" if selected == "Claude" else ""), "RadioButtonControl", group),
                          selected == "Code")
    return claude, code


class SidebarTests(unittest.TestCase):
    def test_a_row_with_a_subtitle_is_listed(self):
        root = node()
        bar = node("Sidebar", parent=root)
        title = "Kasuj pliki Storage przy usuwaniu podopiecznego"
        sidebar_row(bar, title, title + " Started from a suggestion in Zakładka nutrition/dieta")
        _, entries = discover(root)
        self.assertEqual([e["key"] for e in entries], ["code:" + title])

    def test_the_mode_switch_names_the_kind_of_the_rows(self):
        for selected, kind in (("Claude", "chat"), ("Code", "code")):
            with self.subTest(selected=selected):
                root = node()
                mode_switch(root, selected)
                sidebar_row(node("Sidebar", parent=root), "Suchość powietrza w pokoju")
                _, entries = discover(root)
                self.assertEqual([e["key"] for e in entries], [kind + ":Suchość powietrza w pokoju"])

    def test_a_chat_of_the_other_mode_is_reached_through_the_mode_switch(self):
        ui = ClaudeUI(worker())
        chats = node()
        _, code = mode_switch(chats, "Claude")
        sidebar_row(node("Sidebar", parent=chats), "Suchość powietrza w pokoju")
        sessions = node()
        mode_switch(sessions, "Code")
        sidebar_row(node("Sidebar", parent=sessions), "Beta")
        opened = pane("Beta")
        screens = {"now": chats}
        clicked = []

        def click(n):
            clicked.append(n.name)
            screens["now"] = sessions if n is code else opened.root
        ui.snapshot = lambda: screens["now"]
        ui.click = click
        with patch("session_automation.time.sleep", lambda s: None):
            found = ui.resolve("code:Beta")
        self.assertEqual(found.key, "code:Beta")
        self.assertEqual(clicked, ["Code, working", "Beta"])


# ------------------------------------------------------------------ cards in the transcript

def question_card(p, labels=("Option A (Recommended)", "Option B"), picked=0, multi=False, primary="Submit",
                  turn=None, pages=False):
    """The 2.37 question card (15.10): in the last message, a group named after the
    question; one toggle button per option (label and description texts), "Other" a level
    down, "Skip" and "Submit". The first option starts out picked. A card with several
    questions also pages through them with "Show previous / next question"."""
    turn = turn or message(p, 2)
    group = node("Test question?", parent=turn)
    if pages:
        pager = node(parent=group)
        node("Show previous question", "ButtonControl", pager)
        node("1 of 2", "TextControl", pager)
        node("Show next question", "ButtonControl", pager)
    node("Test question?", "TextControl", group)
    states = []
    for i, label in enumerate(labels):
        button = node(label + " Choose " + label + ".", "ButtonControl", group)
        node(label, "TextControl", button)
        node("Choose " + label + ".", "TextControl", button)
        states.append(toggle(button, 1 if i == picked else 0))
    other = node("Other", "ButtonControl", node(parent=group))
    states.append(toggle(other))
    node("Skip", "ButtonControl", group)
    node(primary, "ButtonControl", group)
    node("Needs your input", "TextControl", turn)
    group.multi = multi
    return group, states


def permission_request(p, older=False, turn=None):
    """The 2.37 permission card (15.10): in the last message, a group "Permission request:
    write" with the question as a run of texts, a description, the change, "Deny", and
    "Always allow , Applies to this session only" next to "More allow options"."""
    turn = turn or message(p, 2)
    wrapper = node(parent=turn)
    node("Permission requested: write. C:\\Temp\\permission-test.txt", "TextControl", wrapper)
    card = node("Permission request: write", parent=wrapper)
    for text in ("Allow Claude to ", "write", "permission-test.txt", "?"):
        node(text, "TextControl", card)
    description = node("Description", parent=card)
    node("Path is outside allowed working directories", "TextControl", description)
    deny = node("Deny", "ButtonControl", card)
    more = node("More allow options", parent=card)
    always = node("Always allow , Applies to this session only", "ButtonControl", more)
    node("Always allow", "TextControl", always)
    node(", Applies to this session only", "TextControl", always)
    node("More allow options", "ButtonControl", more)
    if older:
        message(p, 3, ("You said: dalej", "TextControl"))
    return card, deny, always


class InlineQuestionTests(unittest.TestCase):
    def setUp(self):
        self.pane = pane("Desktop helper UI test")
        message(self.pane, 1, ("You said: This is a short UI test", "TextControl"))
        self.ui = ClaudeUI(worker())
        self.ui.resolve = lambda *a, **kw: self.pane
        self.ui.value = lambda n: ""
        self.calls = []
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def card(self, **kwargs):
        self.group, self.states = question_card(self.pane, **kwargs)
        self.ui.click = self.clicked
        return self.group

    def clicked(self, n):
        """Single choice moves the pick (radio-like); multiple choice flips one option;
        "Other" opens its text box; Submit / Next sends the card away."""
        self.calls.append(button_name(n))
        if n.name in ("Submit", "Next"):
            return
        options = [b for b in self.group.walk() if b.type == "ButtonControl" and b.control is not None]
        if self.group.multi:
            n.control.GetTogglePattern().ToggleState ^= 1
        else:
            for b in options:
                b.control.GetTogglePattern().ToggleState = 1 if b is n else 0
        edits = [e for e in self.group.walk() if e.type == "EditControl"]
        if n.name == "Other" and not edits:
            node("Other option", "EditControl", self.group)
        elif n.name != "Other" and edits and not self.group.multi:
            edits[0].parent.children.remove(edits[0])

    def answer(self):
        return self.ui.answer(self.pane.key, question_in(self.pane)["fingerprint"])

    def test_the_card_in_the_last_message_is_the_question(self):
        self.card()
        found = question_in(self.pane)
        self.assertEqual(found["labels"], ["Option A (Recommended)", "Option B", "Other"])
        self.assertEqual(len(found["recommended"]), 1)
        self.assertEqual(found["submit"].name, "Submit")

    def test_a_card_in_an_earlier_message_is_history(self):
        self.card()
        message(self.pane, 3, ("You said: dalej", "TextControl"))
        self.assertIsNone(question_in(self.pane))

    def test_an_answered_question_is_not_a_question(self):
        message(self.pane, 2, ("Claude responded: The question came back as Option A.", "TextControl"),
                ("Test question?", "TextControl"), ("Option A (Recommended)", "TextControl"))
        self.assertIsNone(question_in(self.pane))

    def test_the_recommended_option_picked_from_the_start_is_just_submitted(self):
        self.card()
        self.assertTrue(self.answer())
        self.assertEqual(self.calls, ["Submit"])

    def test_the_recommended_option_is_picked_when_another_one_starts_picked(self):
        self.card(labels=("Option B", "Option A (Recommended)"))
        self.assertTrue(self.answer())
        self.assertEqual(self.calls, ["Option A (Recommended)", "Submit"])

    def test_without_a_recommendation_claude_is_asked_to_choose(self):
        typed = []
        self.ui.type_into = lambda n, text: typed.append((n.name, text))
        self.card(labels=("Szybko", "Wolno"))
        self.assertTrue(self.answer())
        self.assertEqual(self.calls, ["Other", "Submit"])
        self.assertEqual(typed, [("Other option", "Pick your recommended option(s).")])

    def test_two_recommendations_on_a_single_choice_card_ask_claude_to_choose(self):
        self.ui.type_into = lambda n, text: None
        self.card(labels=("Szybko (Recommended)", "Wolno (Recommended)"))
        self.assertTrue(self.answer())
        self.assertEqual(self.calls[-2:], ["Other", "Submit"])

    def test_multiple_choice_gets_every_recommendation(self):
        self.card(labels=("Siła (Recommended)", "Masa", "Wytrzymałość (Recommended)"), multi=True, primary="Next")
        self.assertTrue(self.answer())
        self.assertEqual(self.calls, ["Wytrzymałość (Recommended)", "Next"])

    def test_a_draft_in_other_is_left_alone(self):
        self.card()
        node("Other option", "EditControl", self.group)
        self.ui.value = lambda n: "moja odpowiedź"
        with self.assertRaisesRegex(RuntimeError, "draft answer"):
            self.answer()
        self.assertEqual(self.calls, [])


def button_name(n):
    texts = [c.name for c in n.walk() if c.type == "TextControl"]
    return texts[0] if texts else n.name


class InlinePermissionTests(unittest.TestCase):
    def test_the_card_in_the_last_message_is_found(self):
        p = pane("Desktop helper UI test")
        card, deny, always = permission_request(p)
        found = permission_in(p)
        self.assertIs(found["card"], card)
        self.assertIs(found["always"], always)
        self.assertIsNone(found["once"])
        self.assertEqual(found["title"], "Allow Claude to write permission-test.txt?")

    def test_a_card_in_an_earlier_message_is_not_live(self):
        p = pane("Desktop helper UI test")
        permission_request(p, older=True)
        self.assertIsNone(permission_in(p))

    def test_the_quick_search_knows_the_new_button_name(self):
        self.assertTrue(allow_name("Always allow , Applies to this session only"))
        self.assertTrue(allow_name("Allow once 2"))
        self.assertFalse(allow_name("More allow options"))

    def test_a_question_card_is_not_a_permission(self):
        p = pane("Desktop helper UI test")
        question_card(p, labels=("Deny", "Always allow (Recommended)"))
        self.assertIsNone(permission_in(p))

    def test_the_card_text_is_no_limit_and_no_error(self):
        p = pane("Desktop helper UI test")
        permission_request(p)
        state = signals(p, app.detect_limit_banner)
        self.assertFalse(state["limit"])
        self.assertIsNone(state["error"])
        self.assertTrue(state["permission"])


def long_turn(p, parts=3):
    """A long turn as Claude 2.37 shows it (measured live on 15.10, an agent asking a
    question after an hour of work): the transcript is a list whose older entries are not
    loaded ("Load earlier messages"); the turn's first part is the article "Currently
    streaming message", the rest follow as plain groups behind "Load the rest of this
    message". Returns the list and the turn's last part, where its cards show."""
    feed = next(n for n in p.root.walk() if n.name == "Chat messages")
    hint = node(parent=feed)
    node("Use the up and down arrow keys to move between messages.", "TextControl", hint)
    entries = node(parent=feed)
    node(parent=entries)
    node("Load earlier messages", "ButtonControl", entries)
    first = node(parent=entries)
    article = node("Currently streaming message", parent=first)
    article.role = "article"
    node("Claude responded: I'll start with the tests.", "TextControl", node(parent=article))
    part = first
    for i in range(parts):
        node("Load the rest of this message", "ButtonControl", entries)
        part = node(parent=entries)
        node(f"Ran the tests, part {i + 1}.", "TextControl", node(parent=part))
    return entries, part


def entry(entries, *texts):
    """One more entry at the end of the transcript list, holding these texts."""
    part = node(parent=entries)
    for text in texts:
        node(text, "TextControl", part)
    return part


class LongTurnTests(unittest.TestCase):
    """Cards at the end of a long turn sit in its last part, outside its article (15.10)."""

    def setUp(self):
        self.pane = pane("Graphify initialization")
        self.entries, self.last = long_turn(self.pane)

    def test_a_question_at_the_end_of_a_long_turn_is_found(self):
        question_card(self.pane, turn=self.last)
        found = question_in(self.pane)
        self.assertIsNotNone(found)
        self.assertEqual(found["labels"], ["Option A (Recommended)", "Option B", "Other"])
        self.assertEqual(found["submit"].name, "Submit")

    def test_the_pages_of_a_card_with_several_questions_are_no_options(self):
        question_card(self.pane, turn=self.last, pages=True, primary="Next")
        found = question_in(self.pane)
        self.assertEqual(found["labels"], ["Option A (Recommended)", "Option B", "Other"])
        self.assertEqual(found["submit"].name, "Next")

    def test_a_question_followed_by_your_message_is_history(self):
        question_card(self.pane, turn=self.last)
        entry(self.entries, "You said: dalej")
        self.assertIsNone(question_in(self.pane))

    def test_a_permission_at_the_end_of_a_long_turn_is_found(self):
        card, deny, always = permission_request(self.pane, turn=self.last)
        found = permission_in(self.pane)
        self.assertIs(found["card"], card)
        self.assertIs(found["always"], always)

    def test_a_limit_card_at_the_end_of_a_long_turn_is_found(self):
        for name, kind in LIMIT_CARD:
            node(name, kind, self.last)
        self.assertEqual(final_limit_card(self.pane, app.parse_reset_time), (True, None))

    def test_a_limit_card_the_turn_went_on_after_is_history(self):
        # "Try again" sends no message of yours: the retried work simply follows the card.
        for name, kind in LIMIT_CARD:
            node(name, kind, self.last)
        entry(self.entries, "Retried: the tests pass now.")
        self.assertFalse(final_limit_card(self.pane, app.parse_reset_time)[0])

    def test_the_marker_at_the_end_of_a_long_reply_is_read(self):
        entry(self.entries, "HANDOVER READY 20261015-130000: C:\\work\\.handover\\HANDOVER-20261015-130000.md")
        self.assertIn("HANDOVER READY 20261015-130000", last_reply_text(self.pane))

    def test_a_reply_ends_where_your_message_begins(self):
        entry(self.entries, "You said: [Auto-Resume] Your context window is nearly full.")
        self.assertEqual(last_reply_text(self.pane), "")


class PagedQuestionTests(InlineQuestionTests):
    """The answering rules hold on a card that pages through several questions."""

    def card(self, **kwargs):
        return super().card(pages=True, **kwargs)


# ------------------------------------------------------------------ context for the handover

def session_rail(p, *texts, expanded_group=None):
    """The "Session details" panel next to a 2.37 conversation."""
    rail = node("Session details", parent=p.root)
    if expanded_group:
        group = node("Context", parent=rail)
        node("Context", "ButtonControl", group)
        for text in expanded_group:
            node(text, "TextControl", group)
    for text in texts:
        group = node(text, parent=rail)
        node(text, "TextControl", group)
        node(text, "ButtonControl", group)
    return rail


class ContextTests(unittest.TestCase):
    def test_the_folded_context_section_gives_a_share(self):
        p = pane()
        session_rail(p, "Environment", "Context 44%", "Connectors 6 connected")
        self.assertEqual(signals(p, app.detect_limit_banner)["context"], dict(used=None, window=None, pct=44))

    def test_the_open_context_section_gives_the_tokens(self):
        p = pane()
        session_rail(p, "Environment", expanded_group=("45% used", "453.6k / 1M", "Messages", "382.1k", "38.2%"))
        self.assertEqual(signals(p, app.detect_limit_banner)["context"],
                         dict(used=453_600, window=1_000_000, pct=45))

    def test_numbers_elsewhere_in_the_panel_are_not_the_context(self):
        p = pane()
        session_rail(p, "Outputs", "2 / 5", "Changes 817 additions")
        self.assertIsNone(signals(p, app.detect_limit_banner)["context"])

    def test_a_meter_with_the_context_still_wins(self):
        p = pane()
        node("Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit", "ButtonControl", p.root)
        session_rail(p, "Context 44%")
        self.assertEqual(signals(p, app.detect_limit_banner)["context"]["used"], 195_800)


class ClaudeLogContextTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "C--project"))

    def log(self, name, *records):
        path = os.path.join(self.root, "C--project", name + ".jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
        return path

    @staticmethod
    def reply(cache_read, created=0, sidechain=False):
        return {"type": "assistant", "isSidechain": sidechain, "timestamp": "2026-10-15T06:16:43.189Z",
                "message": {"model": "claude-opus-5-5", "usage": {
                    "input_tokens": 2, "cache_creation_input_tokens": created,
                    "cache_read_input_tokens": cache_read, "output_tokens": 4683}}}

    def test_the_last_reply_of_the_titled_log_gives_the_context(self):
        self.log("7f71a3cc", {"type": "custom-title", "customTitle": "Obsługa wyczerpania tokenów"},
                 self.reply(400_000), self.reply(468_721, 7_190),
                 self.reply(90_000, sidechain=True),                  # a subagent's own context
                 {"type": "custom-title", "customTitle": "Obsługa wyczerpania tokenów"})
        self.assertEqual(QuotaLog(self.root).context_for("Obsługa wyczerpania tokenów"),
                         dict(used=475_913, window=None, pct=None))

    def test_a_log_without_a_reply_yet_gives_no_context(self):
        self.log("abc", {"type": "custom-title", "customTitle": "Nowa rozmowa"})
        self.assertIsNone(QuotaLog(self.root).context_for("Nowa rozmowa"))

    def test_another_title_is_not_this_conversation(self):
        self.log("abc", {"type": "custom-title", "customTitle": "Desktop helper UI test"}, self.reply(5_000))
        self.assertIsNone(QuotaLog(self.root).context_for("Obsługa wyczerpania tokenów"))

    def test_a_renamed_conversation_counts_under_its_newest_title(self):
        self.log("abc", {"type": "custom-title", "customTitle": "Stary tytuł"}, self.reply(5_000),
                 {"type": "custom-title", "customTitle": "Nowy tytuł"})
        log = QuotaLog(self.root)
        self.assertIsNone(log.context_for("Stary tytuł"))
        self.assertEqual(log.context_for("Nowy tytuł")["used"], 5_002)

    def test_the_scan_adds_the_logged_tokens_to_the_panels_share(self):
        w = worker()
        w.cfg.update(handover_enabled=True, auto_send=True)
        engine = SessionEngine(w, ClaudeUI(w), quota=FakeQuota())
        engine.quota.context_for = lambda title: dict(used=475_913, window=None, pct=None)
        p = pane("Obsługa wyczerpania tokenów")
        session_rail(p, "Context 48%")
        engine.ui.resolve = lambda key, navigate=False: p
        seen = {}
        engine.step = lambda key, state, observed, now: seen.update(observed)
        engine.after_observe = lambda *a: None
        engine.scan_one(p.key, SessionState(), dt.datetime(2026, 10, 15, 8, 30))
        self.assertEqual(seen["context"], dict(used=475_913, window=None, pct=48))


# ------------------------------------------------------------------ the usage panel

class UsageBarTests(unittest.TestCase):
    """15.10: the usage popover shows each limit as a progress bar ("18% used")."""

    def test_the_bars_give_the_percentages(self):
        def bar(name, value):
            ctrl = types.SimpleNamespace(Name=name, ControlTypeName="ProgressBarControl",
                                         GetValuePattern=lambda: types.SimpleNamespace(Value=value))
            return ctrl, 2
        texts = [(types.SimpleNamespace(Name=n, ControlTypeName="TextControl"), 2) for n in ("Usage", "Usage limits")]
        controls = [*texts, (types.SimpleNamespace(Name="Session limit", ControlTypeName="TextControl"), 2),
                    bar("Session limit", "18% used"),
                    (types.SimpleNamespace(Name="Resets in 4 hr 47 min", ControlTypeName="TextControl"), 2),
                    (types.SimpleNamespace(Name="Weekly", ControlTypeName="TextControl"), 2),
                    bar("Weekly", "30% used"),
                    (types.SimpleNamespace(Name="Resets Mon 6:00 PM", ControlTypeName="TextControl"), 2)]
        with patch.object(app.auto, "WalkControl", lambda *a, **kw: iter(controls)):
            rows = app.MonitorWorker._parse_usage_rows(object())
        self.assertEqual((rows["5h"]["pct"], rows["weekly"]["pct"]), (18, 30))
        self.assertIn("reset", rows["5h"])


class PaceWarningTests(unittest.TestCase):
    """20.10: with the weekly limit getting close, a status bar beside the conversation says
    "On pace to hit your weekly limit early". Nothing is blocked: read as a limit, it had the
    program type its resume message every ten minutes into conversations that were waiting
    for an answer."""

    def window(self, *texts):
        p = pane("RIR podopiecznych w widokach trenera")
        status = node("", "StatusBarControl", node(parent=p.root))
        for text in texts:
            node(text, "TextControl", status)
        return p

    def test_the_pace_warning_is_no_limit(self):
        p = self.window("On pace to hit your weekly limit early")
        self.assertFalse(signals(p, app.detect_limit_banner)["limit"])

    def test_a_real_notice_beside_it_still_counts(self):
        p = self.window("On pace to hit your weekly limit early", "Weekly limit reached")
        self.assertTrue(signals(p, app.detect_limit_banner)["limit"])


# ------------------------------------------------------------------ starting a conversation

def new_screen(environment="auto-resume"):
    """The 2.37 new-session screen (15.10): the worktree suggestion, the composer, and
    under it "Environment: <folder>", "Permissions: …", "Model: …" and the meter."""
    root = node()
    bar = node("Sidebar", parent=root)
    wide = node("New", "ButtonControl", bar)
    wide.rect = (0, 78, 261, 104)
    small = node("New session", "ButtonControl", bar)
    small.rect = (237, 204, 261, 228)
    screen = node(parent=root)
    node("Turn on Worktree Session running here", "ButtonControl", screen)
    node("Prompt", "EditControl", screen)
    node("Add", "ButtonControl", screen)
    node("Environment: " + environment, "ButtonControl", screen)
    node("Permissions: Bypass permissions", "ButtonControl", screen)
    node("Model: Opus 5.5 Extra", "ButtonControl", screen)
    return root


class NewSessionScreenTests(unittest.TestCase):
    def setUp(self):
        self.ui = ClaudeUI(worker())

    def test_the_environment_names_the_folder(self):
        prompt, project = self.ui.new_session_screen(new_screen("aplikacja fitness"))
        self.assertEqual(prompt.name, "Prompt")
        self.assertEqual(self.ui.project_name(project), "aplikacja fitness")

    def test_the_wide_new_button_starts_the_conversation(self):
        root = new_screen()
        buttons = self.ui.new_chat_buttons(root)
        self.assertEqual(sorted(b.name for b in buttons), ["New", "New session"])
        self.assertEqual(self.ui.one_new_button(buttons).name, "New")

    def test_the_environment_is_picked_from_the_more_submenu(self):
        """"auto-resume , edit environment, right arrow" sits under "More 6" (15.10)."""
        menu = node()
        top = node("Environment: aplikacja fitness", "MenuControl", menu)
        top.role = "menu"
        local = node("Local", parent=top)
        node("This computer", "RadioButtonControl", local)
        fitness = node("aplikacja fitness , edit environment, right arrow", "RadioButtonControl", local)
        node("aplikacja fitness", "TextControl", fitness)
        more = node("More 6", "MenuItemControl", top)
        submenu = node()
        sub = node("More 6", "MenuControl", submenu)
        sub.role = "menu"
        item = node("auto-resume , edit environment, right arrow", "RadioButtonControl", sub)
        node("auto-resume", "TextControl", item)
        node(", edit environment, right arrow", "TextControl", item)
        screens = {"now": menu}
        clicked = []

        def click(n):
            clicked.append(n.name)
            if n is more:
                top.children.append(sub)
                sub.parent = top
        self.ui.snapshot = lambda: screens["now"]
        self.ui.click = click
        self.ui.press = lambda keys, wait=0.1: clicked.append(keys)
        with patch("session_automation.time.sleep", lambda s: None):
            self.assertTrue(self.ui.pick_project(node("Environment: aplikacja fitness", "ButtonControl"), "auto-resume"))
        self.assertEqual(clicked, ["Environment: aplikacja fitness", "More 6",
                                   "auto-resume , edit environment, right arrow"])


class LiveCheckTests(unittest.TestCase):
    """tools/live_check.py prints what the program reads, never the conversation's text."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "live_check", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "live_check.py"))
        self.tool = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", list(sys.path)):
            spec.loader.exec_module(self.tool)

    def test_an_error_is_shown_without_its_text(self):
        # The error can be the transcript's last text, the conversation's own words (review, 15.10).
        for permanent, shown in ((False, "error=yes"), (True, "error=permanent")):
            with self.subTest(permanent=permanent):
                line = self.tool.flags(dict(limit=False, error="API Error: 500 tajny fragment rozmowy",
                                            permanent=permanent))
                self.assertNotIn("tajny", line)
                self.assertIn(shown, line)
        self.assertIn("error=None", self.tool.flags(dict(error=None, permanent=False)))


if __name__ == "__main__":
    unittest.main()
