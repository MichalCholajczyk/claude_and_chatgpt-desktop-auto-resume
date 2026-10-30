"""Claude 4.29 (measured live on 30.10) and the stall of 27.10.

On 27.10 Auto-Resume watched "Case Study: KPI tracking dla przedstawicieli handlowych", the
conversation that had split its work into stage sessions, and "Etap 2", one of them. It lost
the case study ("Unavailable — open it in Claude"), and its header stood at "Next attempt at
15:58:39", 00:00:00, until Stop. What met there:

- A session that started others lists them under itself in the sidebar, and its row holds a
  "Hide nested sessions <title>" button. The button ends with the title too, so the row read
  as two and was left out: the conversation could no longer be opened from the sidebar.
- A conversation that cannot be found kept its overdue attempt, and the header kept
  counting down to it.
- Both conversations were at work, yet both got "Server error — retry scheduled". An error
  a working conversation shows is no stop, and the program read more of the window than the
  conversation: the sidebar, the session details beside it, and the panel beside it, whose
  preview tile draws its own "Retry" when the dev server behind it has stopped (both
  conversations had used the app's browser on local servers that day).

Every tree here copies a measured one, or the 4.29 bundle where noted; nothing touches a real
window."""
import contextlib
import datetime as dt
import queue
import types
import unittest
from unittest.mock import patch

import chatgpt_auto_continue as gpt
import claude_auto_continue as app
from chatgpt_automation import ChatGPTEngine
from input_guard import InputPaused
from session_automation import LOADING, ClaudeUI, Node, Pane, SessionEngine, SessionState, discover, signals
from .test_chatgpt import FakeLog, FixtureUI as ChatGPTFixtureUI, Window as ChatGPTWindow
from .test_new_claude_ui import mode_switch, session_rail
from .test_sessions import NOW, FakeQuota, FixtureUI, node, observed, pane
from .test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


PARENT = "Case Study: KPI tracking dla przedstawicieli handlowych"
CHILDREN = ("Etap 2: powłoka UI i system projektowy", "Etap 1A: silnik punktacji i dane")


def sidebar(root):
    """The list part of the 4.29 sidebar: a day's section of rows."""
    return node(parent=node(parent=node("Sidebar", parent=root)))


def plain_row(section, title):
    """A row of a session on its own (30.10): the button holds the title and a status bar,
    "More options for <title>" sits beside it."""
    row = node(parent=node(parent=section))
    button = node(title, "ButtonControl", row)
    texts = node(parent=node(parent=button))
    node(title, "TextControl", texts)
    node("", "StatusBarControl", texts)
    node("More options for " + title, "ButtonControl", node(parent=row))
    return button


def family(section, parent, children=(), status="", folded=False):
    """A session that started others, as measured on 30.10: its row, whose button also holds
    "Hide nested sessions <title>" ("Show …" once folded), then a group of the sessions it
    started, each "<title> Started by Claude from <parent>". Folded, the group is gone.
    Returns the row buttons by title."""
    row = node(parent=node(parent=section))
    button = node(" ".join(filter(None, (parent, status))), "ButtonControl", row)
    texts = node(parent=node(parent=button))
    node(parent, "TextControl", texts)
    bar = node("", "StatusBarControl", texts)
    if status:
        node(status, "TextControl", bar)
    node(("Show" if folded else "Hide") + " nested sessions " + parent, "ButtonControl", node(parent=button))
    node("More options for " + parent, "ButtonControl", node(parent=row))
    buttons = {parent: button}
    if not folded and children:
        nested = node(parent=section)
        for child in children:
            row = node(parent=node(parent=nested))
            buttons[child] = node(child + " Started by Claude from " + parent, "ButtonControl", row)
            texts = node(parent=buttons[child])
            node(child, "TextControl", texts)
            node("Started by Claude from " + parent, "TextControl", texts)
            node("", "StatusBarControl", texts)
            node("More options for " + child, "ButtonControl", node(parent=row))
    return buttons


class NestedSidebarTests(unittest.TestCase):
    def listed(self, root):
        return {e["key"]: e["node"] for e in discover(root)[1]}

    def test_a_session_that_started_others_is_listed(self):
        root = node()
        buttons = family(sidebar(root), PARENT, CHILDREN)
        rows = self.listed(root)
        self.assertEqual(list(rows), ["code:" + PARENT, *("code:" + child for child in CHILDREN)])
        self.assertIs(rows["code:" + PARENT], buttons[PARENT])       # the row, never its toggle

    def test_one_with_a_status_is_listed_too(self):
        # 30.10: "Historia ćwiczeń i skala RIR Unread response", above its nested session.
        root = node()
        family(sidebar(root), "Historia ćwiczeń i skala RIR", ("RIR podopiecznych w widokach trenera",),
               status="Unread response")
        self.assertIn("code:Historia ćwiczeń i skala RIR", self.listed(root))

    def test_a_folded_family_is_listed(self):
        root = node()
        family(sidebar(root), PARENT, CHILDREN, folded=True)
        self.assertEqual(list(self.listed(root)), ["code:" + PARENT])

    def test_a_session_named_like_the_toggle_is_listed(self):
        # The toggle is told by the row it belongs to, not by its first words.
        root = node()
        section = sidebar(root)
        plain_row(section, "Show nested sessions in the picker")
        family(section, "Hide nested sessions everywhere", ("Picker: test",))
        self.assertEqual(list(self.listed(root)), ["code:Show nested sessions in the picker",
                                                   "code:Hide nested sessions everywhere", "code:Picker: test"])

    def test_two_sessions_of_one_title_stay_ambiguous(self):
        # 30.10: two "Projekt zawiesza się przy odtwarzaniu wideo", one of them with a nested
        # session. Seeing only the other one, the program would have opened whichever was left.
        title = "Projekt zawiesza się przy odtwarzaniu wideo"
        root = node()
        section = sidebar(root)
        family(section, title, ("Make long-cache headers apply on Hostinger LiteSpeed",))
        plain_row(section, title)
        self.assertEqual([e["key"] for e in discover(root)[1]].count("code:" + title), 2)
        ui = ClaudeUI(app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG)))
        clicked = []
        ui.snapshot, ui.click = (lambda: root), clicked.append
        with patch("session_automation.time.sleep", lambda s: None):
            self.assertIsNone(ui.resolve("code:" + title))
        self.assertEqual(clicked, [])


class Window:
    """The Claude window of 27.10, read again on every scan: the conversation `open`, and a
    sidebar listing the case study's family. Clicking a row opens its conversation."""

    def __init__(self):
        self.open, self.clicked = PARENT, []

    def snapshot(self):
        root = node()
        self.rows = {button: title for title, button in family(sidebar(root), PARENT, CHILDREN).items()}
        pane(self.open, parent=root)
        return root

    def click(self, n):
        self.clicked.append(n.name)
        self.open = self.rows.get(n, self.open)


class LostCaseStudyTests(unittest.TestCase):
    """The scans of 27.10: the case study (open), "Etap 2" (opened from the sidebar), then the
    case study again, which the sidebar no longer seemed to list."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.logged = []
        self.worker.log = lambda key, level="info", **kw: self.logged.append(self.worker.t(key, **kw))
        self.worker.state = self.worker.MONITORING
        self.worker.cfg.update(watch_scope="selected", selected_chats=["code:" + PARENT, "code:" + CHILDREN[0]])
        self.window = Window()
        ui = ClaudeUI(self.worker)
        ui.snapshot, ui.click = self.window.snapshot, self.window.click
        self.engine = SessionEngine(self.worker, ui, quota=FakeQuota())
        # The running Auto-Resume holds this lock while it acts: the tests stay off it.
        for patcher in (patch("session_automation.input_lock", lambda cancelled: contextlib.nullcontext()),
                        patch("session_automation.time.sleep", lambda s: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan(self, times):
        for _ in range(times):
            self.engine.next_scan = 0
            self.engine.tick(NOW)

    def test_the_conversation_that_started_the_others_is_opened_again(self):
        self.scan(3)
        self.assertEqual(self.window.clicked, [CHILDREN[0] + " Started by Claude from " + PARENT, PARENT])
        self.assertEqual(self.window.open, PARENT)
        self.assertEqual(self.logged, [])

    def test_a_window_read_is_said_so_even_when_its_conversation_is_gone(self):
        # Else an old "covered or minimized" would stay in the header over the lost one.
        self.worker.cfg["selected_chats"] = ["code:Zarchiwizowana rozmowa"]
        self.scan(1)
        statuses = []
        while not self.worker.out.empty():
            kind, data = self.worker.out.get()
            if kind == "status":
                statuses.append(data)
        self.assertEqual(statuses, ["ok"])


class Resolver(FixtureUI):
    """Finds the conversations of `found`; a missing one is not there."""

    def __init__(self):
        super().__init__()
        self.found = {}

    def resolve(self, key, navigate=True):
        return self.found.get(key)


class LostConversationTests(unittest.TestCase):
    """A conversation the scan cannot find keeps what it waits for, but its time is no
    countdown: nothing can be done for it until it is back."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.worker.state = self.worker.MONITORING
        self.ui = Resolver()
        self.engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())
        self.key = "code:" + PARENT
        # 15:58:09 on 27.10: a retry was due half a minute later.
        self.state = self.engine.sessions[self.key] = SessionState(
            phase="waiting", reason="api", due=NOW - dt.timedelta(minutes=1))

    def last_state(self):
        states = []
        while not self.worker.out.empty():
            kind, data = self.worker.out.get()
            if kind == "state":
                states.append(data)
        return states[-1]

    def test_it_holds_no_countdown(self):
        self.engine.scan_one(self.key, self.state, NOW)
        self.engine.publish()
        shown = self.last_state()
        self.assertEqual((shown["state"], shown["send_at"]), ("MONITORING", None))
        self.assertEqual(shown["unavailable"], [PARENT])
        self.assertEqual(self.state.phase, "waiting")              # its retry is kept for later

    def test_another_conversations_attempt_still_shows(self):
        self.engine.scan_one(self.key, self.state, NOW)
        later = NOW + dt.timedelta(hours=2)
        self.engine.sessions["code:" + CHILDREN[0]] = SessionState(phase="waiting", reason="limit",
                                                                   reset=later, due=later)
        self.engine.publish()
        shown = self.last_state()
        self.assertEqual((shown["state"], shown["send_at"]), ("ARMED", later))

    def test_found_again_it_gets_its_turn(self):
        self.engine.scan_one(self.key, self.state, NOW)
        p = pane(PARENT)
        node("API Error: 529 overloaded_error", "TextControl", p.root)
        self.ui.found[self.key] = p
        self.engine.scan_one(self.key, self.state, NOW)
        self.assertEqual(self.ui.calls, [(self.key, False, True)])
        self.engine.publish()
        self.assertEqual(self.last_state()["unavailable"], [])

    def test_found_again_its_row_no_longer_says_unavailable(self):
        self.engine.scan_one(self.key, self.state, NOW)
        self.assertEqual(self.state.notice, "unavailable")
        self.ui.found[self.key] = pane(PARENT)
        self.engine.scan_one(self.key, self.state, NOW)
        self.assertNotEqual(self.state.notice, "unavailable")

    def test_looking_for_it_failing_holds_no_countdown_either(self):
        # A row Claude reports as off screen cannot be clicked: the scan fails before reading.
        def fail(key, navigate=True):
            raise RuntimeError("Control is unavailable")
        self.ui.resolve = fail
        with self.assertRaises(RuntimeError):
            self.engine.scan_one(self.key, self.state, NOW)
        self.engine.publish()
        shown = self.last_state()
        self.assertEqual((shown["state"], shown["send_at"], shown["unavailable"]), ("MONITORING", None, [PARENT]))

    def test_a_scan_put_off_loses_nothing(self):
        for error in (InputPaused("Someone is using the mouse; action deferred"),
                      RuntimeError("Pending command; action deferred"),
                      RuntimeError("Accessibility tree changed; retry on next scan")):
            with self.subTest(error=str(error)):
                def put_off(key, navigate=True):
                    raise error
                self.ui.resolve = put_off
                with self.assertRaises(type(error)):
                    self.engine.scan_one(self.key, self.state, NOW)
                self.engine.publish()
                shown = self.last_state()
                self.assertEqual((shown["state"], shown["send_at"], shown["unavailable"]),
                                 ("ARMED", self.state.due, []))


def loading_pane(title=PARENT, parent=None):
    """A conversation 4.29 has just opened (bundle): its title and composer, the preview tile
    beside it with a stopped server, and in the transcript's place "Loading session"."""
    root = node(parent=parent)
    node(title + ", rename session", "ButtonControl", node(parent=root))
    body = node(parent=root)
    node(LOADING, "TextControl", node("", "StatusBarControl", body))
    prompt = node("Prompt", "EditControl", body)
    tile = node("Preview", parent=root)
    tile.role = "region"
    node("Nothing listening on localhost:5173", "TextControl", tile)
    node("Retry", "ButtonControl", tile)
    return Pane("code:" + title, title, "code", root, prompt)


class LoadingTests(unittest.TestCase):
    """A conversation still loading has nothing to read, while the panel beside it has."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self, open_pane):
        root = node()
        plain_row(sidebar(root), PARENT)
        open_pane(root)
        return root

    def test_opening_one_waits_for_its_transcript(self):
        ui = ClaudeUI(self.worker)
        reads = [self.window(lambda root: pane(CHILDREN[0], parent=root)),
                 self.window(lambda root: loading_pane(parent=root)),
                 self.window(lambda root: pane(PARENT, parent=root))]
        ui.snapshot = lambda: reads.pop(0) if len(reads) > 1 else reads[0]
        ui.click = lambda n: None
        found = ui.resolve("code:" + PARENT)
        self.assertIsNotNone(found.column())

    def test_one_still_loading_is_not_read(self):
        ui = Resolver()
        ui.found["code:" + PARENT] = loading_pane()
        engine = SessionEngine(self.worker, ui, quota=FakeQuota())
        state = SessionState()
        engine.scan_one("code:" + PARENT, state, NOW)
        self.assertEqual((state.phase, state.notice, state.lost), ("watching", "", False))

    def test_one_still_loading_is_not_resumed(self):
        ui = ClaudeUI(self.worker)
        ui.resolve = lambda key, navigate=True: loading_pane()
        with self.assertRaisesRegex(RuntimeError, "Conversation still loading"):
            ui.resume("code:" + PARENT, api_error=True)


class ChatGPTLostConversationTests(unittest.TestCase):
    """ChatGPT Auto-Resume scans in its own way, and marks a conversation found again too."""

    def test_found_again_it_is_no_longer_lost(self):
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        worker.log = lambda *a, **kw: None
        worker.state = worker.MONITORING
        ui = ChatGPTFixtureUI(ChatGPTWindow(lang="en").said("user", "Fix it").said("bot", "Done."))
        engine = ChatGPTEngine(worker, ui, quota=FakeLog())
        key = "codex:Fix the parser"
        state = engine.sessions[key] = SessionState()
        found = ui.resolve
        ui.resolve = lambda k, navigate=True: None
        engine.scan_one(key, state, NOW)
        self.assertEqual((state.lost, state.notice), (True, "unavailable"))
        ui.resolve = found
        engine.scan_one(key, state, NOW)
        self.assertEqual((state.lost, state.notice), (False, ""))


class LostConversationCaptionTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(app, "load_config", lambda: dict(app.DEFAULT_CONFIG)),
                        patch.object(app, "save_config", lambda cfg: None),
                        patch.object(app.MonitorWorker, "start", lambda w: None)]
        for p in self.patches:
            p.start()
        self.ui = app.App()
        self.ui.withdraw()

    def tearDown(self):
        self.ui.destroy()
        for p in reversed(self.patches):
            p.stop()

    def caption(self, *lost):
        self.ui._handle_event("state", dict(state="MONITORING", reset_at=None, send_at=None, pause_reason=None,
                                            start_until=None, unavailable=list(lost)))
        self.ui._update_caption()
        return self.ui.lbl_caption.cget("text")

    def test_the_header_says_which_conversation_to_open(self):
        self.assertEqual(self.caption(CHILDREN[0]), app.tr("en", "cap_unavailable", chat=CHILDREN[0]))
        self.ui._set_lang("pl")
        self.assertEqual(self.caption(CHILDREN[0]), app.tr("pl", "cap_unavailable", chat=CHILDREN[0]))

    def test_it_fits_on_its_line(self):
        # The header is one line: a long title is cut short, more conversations are counted.
        short = "Case Study: KPI tracking dla przedstawi…"
        self.assertEqual(self.caption(PARENT), app.tr("en", "cap_unavailable", chat=short))
        self.assertEqual(self.caption(PARENT, *CHILDREN), app.tr("en", "cap_unavailable_more", chat=short, n=2))
        self.ui._set_lang("pl")
        self.assertEqual(self.caption(PARENT, CHILDREN[0]), app.tr("pl", "cap_unavailable_more", chat=short, n=1))


class RowClickTests(unittest.TestCase):
    """A row's own "Hide nested sessions" can lie under its middle, where a click lands."""

    @staticmethod
    def row(toggle, right=1550):
        """The row of 30.10 (x 1289-1550, 280 px sidebar), its toggle at `toggle`."""
        button = Node("Raporty i eksport", "ButtonControl", (1289, 386, right, 418))
        button.children.append(Node("Hide nested sessions Raporty i eksport", "ButtonControl", toggle, parent=button))
        return button

    def test_the_middle_when_nothing_lies_there(self):
        # "Case Study…" as measured: its title cut short, its toggle at the right end.
        self.assertIsNone(ClaudeUI.free_spot(self.row((1502, 393, 1520, 411))))

    def test_beside_the_toggle_when_it_lies_under_the_middle(self):
        # A 17-letter title ends at x 1407, and its toggle covers the middle, x 1419.
        self.assertEqual(ClaudeUI.free_spot(self.row((1409, 393, 1428, 411))), 8)

    def test_the_click_goes_there(self):
        worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        worker.hwnd, worker._focus_window = 5, lambda hwnd: None
        clicks = []
        button = self.row((1409, 393, 1428, 411))
        button.control = types.SimpleNamespace(IsEnabled=True, IsOffscreen=False, Click=lambda **kw: clicks.append(kw))
        plain = Node("Stop", "ButtonControl", (0, 0, 9, 9), button.control)
        with patch.object(app.user32, "GetForegroundWindow", return_value=5):
            ClaudeUI(worker).click(button)
            ClaudeUI(worker).click(plain)
        self.assertEqual(clicks, [dict(x=8, simulateMove=False), dict(x=None, simulateMove=False)])


class ModeSwitchTests(unittest.TestCase):
    def test_a_missing_conversation_is_not_looked_for_in_the_mode_already_shown(self):
        # 27.10: every scan of the lost case study clicked "Code", which was already chosen.
        ui = ClaudeUI(app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG)))
        root = node()
        mode_switch(root, "Code")
        plain_row(sidebar(root), "Strona dla wujasa — wizytówka i galeria")
        clicked = []
        ui.snapshot, ui.click = (lambda: root), clicked.append
        with patch("session_automation.time.sleep", lambda s: None):
            self.assertIsNone(ui.resolve("code:" + PARENT))
        self.assertEqual(clicked, [])


class WorkingConversationTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = FixtureUI()
        self.engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())

    def test_an_error_while_claude_works_is_no_stop(self):
        state = SessionState()
        self.engine.step("code:A", state, observed(error="API Error: 529", busy=True), NOW)
        self.assertEqual((state.phase, state.due, state.notice), ("watching", None, ""))
        self.assertEqual(self.ui.calls, [])

    def test_once_the_turn_has_ended_it_is_retried(self):
        state = SessionState()
        self.engine.step("code:A", state, observed(error="API Error: 529"), NOW)
        self.assertEqual((state.phase, state.reason), ("waiting", "api"))


class BesideTheConversationTests(unittest.TestCase):
    """Only the conversation's own controls count, not what the window shows beside it."""

    def read(self, p):
        s = signals(p, app.detect_limit_banner)
        return dict(error=s["error"], retry=s["retry"], busy=s["busy"], limit=s["limit"])

    NOTHING = dict(error=None, retry=None, busy=False, limit=False)

    def foreign(self, parent):
        """What a web page or another session may well show."""
        node("Something went wrong", "TextControl", parent)
        node("Try again", "ButtonControl", parent)
        node("Stop", "ButtonControl", parent)
        node("Usage limit reached", "TextControl", parent)

    def test_a_page_the_app_shows_is_the_pages(self):
        # 30.10: the case study's own app, open beside the chat, says "Spróbuj ponownie".
        p = pane(PARENT)
        page = node("KPI ScoreCard", "DocumentControl", node(parent=p.root))
        self.foreign(page)
        node("Spróbuj ponownie", "ButtonControl", page)
        self.assertEqual(self.read(p), self.NOTHING)

    def test_a_tile_of_the_panel_beside_the_chat_is_the_tiles(self):
        # 4.29 bundle: with its dev server stopped, the preview tile draws Claude's own card
        # in place of the page, so no document holds it.
        p = pane(PARENT)
        tile = node("Preview", parent=p.root)
        tile.role = "region"
        node("Nothing listening on localhost:5173", "TextControl", tile)
        node("Retry", "ButtonControl", tile)
        self.assertEqual(self.read(p), self.NOTHING)

    def test_a_region_by_the_composer_is_the_conversations(self):
        p = pane(PARENT)
        strip = node("Usage limit", parent=p.prompt.parent)
        strip.role = "region"
        node("Usage limit reached", "TextControl", strip)
        self.assertTrue(self.read(p)["limit"])

    def test_the_artifact_panel_around_its_page_is_no_part_of_the_chat(self):
        p = pane(PARENT)
        panel = node("Artifacts", parent=p.root)
        panel.role = "region"
        self.foreign(node("Artifacts controls", "ToolBarControl", panel))
        self.assertEqual(self.read(p), self.NOTHING)

    def test_the_sidebar_lists_other_sessions(self):
        p = pane(PARENT)
        self.foreign(node(parent=node("Sidebar", parent=p.root)))
        self.assertEqual(self.read(p), self.NOTHING)

    def test_the_session_details_describe_the_folder(self):
        p = pane(PARENT)
        self.foreign(session_rail(p, "Context 44%", "Connectors 6 connected"))
        self.assertEqual(self.read(p), self.NOTHING)
        self.assertEqual(signals(p, app.detect_limit_banner)["context"], dict(used=None, window=None, pct=44))

    def test_the_conversations_own_controls_still_count(self):
        p = pane(PARENT)
        self.foreign(p.root)
        self.assertEqual(self.read(p)["error"], "Something went wrong")
        self.assertIsNotNone(self.read(p)["retry"])
        self.assertTrue(self.read(p)["busy"])


if __name__ == "__main__":
    unittest.main()
