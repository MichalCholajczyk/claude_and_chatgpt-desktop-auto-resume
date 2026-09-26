"""Approving Claude's permission cards ("Allow Claude to fetch …?") — no real input."""
import datetime as dt
import queue
import re
import types
import unittest
from unittest.mock import patch

import claude_auto_continue as app
import session_automation
from session_automation import (ClaudeUI, SessionEngine, SessionState, allow_name, button_label,
                                permission_in, signals)
from test_sessions import NOW, FakeQuota, FixtureUI, claude_question, node, observed, pane
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


FETCH = "Allow Claude to fetch https://support.google.com/googleplay/android-developer/answer/16954621?hl=en?"
RUN = "Allow Claude to run Append two observations to the skill log with collision checks?"


def permission_card(p, labels=("Deny", "Always allow", "More allow options", "Allow once"), title=FETCH,
                    hints_in_name_only=False):
    """Claude's permission card as captured live on 25.09: "Permission request: run" in
    the dock above the composer; the question as a run of texts ("Allow Claude to ",
    "run", " ", what, "?"), what Claude wants to do, why it asks and the command; then
    the buttons, each named by its label and key digit ("Allow once 2")."""
    runs = re.fullmatch(r"(Allow Claude to )(\w+)( )(.*)(\?)", title).groups()
    card = node("Permission request: " + runs[1], parent=p.prompt.parent)
    for text in runs:
        node(text, "TextControl", card)
    for text in (runs[3], "Contains brace with quote character (expansion obfuscation)", '{ echo "probe"; }'):
        node(text, "TextControl", node(parent=card))
    buttons, number = {}, 0
    for label in labels:
        if label == "More allow options":       # the arrow next to "Always allow"
            buttons[label] = node(label, "ButtonControl", card)
            continue
        number += 1
        button = node(f"{label} {number}", "ButtonControl", card)
        if not hints_in_name_only:
            node(label, "TextControl", button)
            node(str(number), "TextControl", button)
        buttons[label] = button
    return card, buttons


class PermissionCardTests(unittest.TestCase):
    def test_card_with_always_allow(self):
        p = pane()
        card, buttons = permission_card(p)
        found = permission_in(p)
        self.assertIs(found["card"], card)
        self.assertIs(found["always"], buttons["Always allow"])
        self.assertIs(found["once"], buttons["Allow once"])
        self.assertEqual(found["title"], FETCH)

    def test_card_without_always_allow(self):
        p = pane()
        _, buttons = permission_card(p, ("Deny", "Allow once"), RUN)
        found = permission_in(p)
        self.assertIsNone(found["always"])
        self.assertIs(found["once"], buttons["Allow once"])
        self.assertEqual(found["title"], RUN)

    def test_key_hints_only_in_the_button_names(self):
        p = pane()
        _, buttons = permission_card(p, hints_in_name_only=True)
        found = permission_in(p)
        self.assertIs(found["always"], buttons["Always allow"])
        self.assertIs(found["once"], buttons["Allow once"])

    def test_same_card_same_fingerprint_other_card_other_fingerprint(self):
        first, second, other = pane(), pane(), pane()
        permission_card(first)
        permission_card(second)
        permission_card(other, ("Deny", "Allow once"), RUN)
        self.assertEqual(permission_in(first)["fingerprint"], permission_in(second)["fingerprint"])
        self.assertNotEqual(permission_in(first)["fingerprint"], permission_in(other)["fingerprint"])

    def test_question_answers_named_like_permission_buttons_are_not_a_permission(self):
        p = pane()
        claude_question(p, ("Deny", "Always allow", "Allow once"))
        self.assertIsNone(permission_in(p))

    def test_question_describing_the_buttons_is_not_a_permission(self):
        # Captured 25.09: an answer's text mentioned "Always allow > Allow once".
        p = pane()
        claude_question(p, ("Tak, działaj (Recommended) Wszystko jak wyżej: Always allow > Allow once",
                            "Bez szybkiego sprawdzania"))
        self.assertIsNone(permission_in(p))

    def test_buttons_in_the_transcript_are_not_a_live_card(self):
        p = pane()
        card, _ = permission_card(p)
        card.parent.children.remove(card)
        chat = next(n for n in p.root.walk() if n.name == "Chat messages")
        card.parent = chat
        chat.children.append(card)
        self.assertIsNone(permission_in(p))

    def test_no_deny_no_permission(self):
        p = pane()
        permission_card(p, ("Always allow", "Allow once"))
        self.assertIsNone(permission_in(p))

    def test_card_text_is_not_read_as_a_limit_or_an_error(self):
        p = pane()
        permission_card(p, ("Deny", "Allow once"),
                        "Allow Claude to run echo 'Usage limit reached' && echo 'API Error: 500'?")
        state = signals(p, app.detect_limit_banner)
        self.assertTrue(state["permission"])
        self.assertFalse(state["limit"])
        self.assertIsNone(state["error"])


class ApprovingUI(FixtureUI):
    def approve(self, key, card):
        self.calls.append(("approve", key, card["fingerprint"]))
        return "always"


class PermissionPolicyTests(unittest.TestCase):
    """When the scheduler approves a card, and that a card never disturbs the schedule."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.logged = []
        self.worker.log = lambda key, level="info", **kw: self.logged.append(self.worker.t(key, **kw))
        self.ui = ApprovingUI()
        self.engine = SessionEngine(self.worker, self.ui)
        self.card = observed(permission=dict(title=FETCH, fingerprint="f"))

    def test_off_by_default(self):
        self.assertFalse(app.DEFAULT_CONFIG["auto_permissions"])
        self.engine.step("code:A", SessionState(), self.card, NOW)
        self.assertEqual(self.ui.calls, [])

    def test_approves_and_logs_what_was_allowed(self):
        self.worker.cfg["auto_permissions"] = True
        self.engine.step("code:A", SessionState(), self.card, NOW)
        self.assertEqual(self.ui.calls, [("approve", "code:A", "f")])
        self.assertEqual(self.logged, ["A: Clicked Always allow — " + FETCH])

    def test_send_automatically_off_blocks_approvals(self):
        self.worker.cfg.update(auto_permissions=True, auto_send=False)
        self.engine.step("code:A", SessionState(), self.card, NOW)
        self.assertEqual(self.ui.calls, [])

    def test_a_card_keeps_the_schedule_and_is_never_typed_into(self):
        self.worker.cfg["auto_permissions"] = True
        due = NOW - dt.timedelta(seconds=1)
        for phase, reason, attempts in (("verifying", "limit", 1), ("waiting", "api", 2)):
            with self.subTest(phase=phase):
                self.ui.calls.clear()
                state = SessionState(phase, reason, due, attempts=attempts)
                self.engine.step("code:A", state, dict(self.card, error="API Error: 529"), NOW)
                self.assertEqual(self.ui.calls, [("approve", "code:A", "f")])     # no resume
                self.assertEqual((state.phase, state.due, state.attempts), (phase, due, attempts))

    def test_a_chat_that_needs_attention_still_gets_approvals(self):
        self.worker.cfg["auto_permissions"] = True
        self.engine.step("code:A", SessionState("exhausted"), self.card, NOW)
        self.assertEqual(self.ui.calls, [("approve", "code:A", "f")])


class ApproveTests(unittest.TestCase):
    """Invoke first (no focus change, no mouse), a real click only when Claude ignored
    it, and never Deny."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.pane = pane()
        self.ui = ClaudeUI(self.worker)
        self.ui.resolve = lambda *a, **kw: self.pane
        self.pressed = []
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def show(self, labels=("Deny", "Always allow", "Allow once"), accepts=("invoke", "click")):
        """Put a card on screen whose buttons react to the given kinds of press."""
        card, buttons = permission_card(self.pane, labels)

        def press(kind, button):
            self.pressed.append((kind, button_label(button)))
            if kind in accepts and card in card.parent.children:
                card.parent.children.remove(card)
        for button in buttons.values():
            button.control = types.SimpleNamespace(
                IsEnabled=True, IsOffscreen=False,
                GetInvokePattern=lambda b=button: types.SimpleNamespace(Invoke=lambda: press("invoke", b)))
        self.ui.click = lambda n: press("click", n)
        return permission_in(self.pane)

    def test_always_allow_is_preferred(self):
        card = self.show()
        self.assertEqual(self.ui.approve(self.pane.key, card), "always")
        self.assertEqual(self.pressed, [("invoke", "Always allow")])

    def test_allow_once_when_it_is_the_only_allow(self):
        card = self.show(("Deny", "Allow once"))
        self.assertEqual(self.ui.approve(self.pane.key, card), "once")
        self.assertEqual(self.pressed, [("invoke", "Allow once")])

    def test_ignored_invoke_falls_back_to_a_real_click(self):
        card = self.show(accepts=("click",))
        self.assertEqual(self.ui.approve(self.pane.key, card), "always")
        self.assertEqual(self.pressed, [("invoke", "Always allow"), ("click", "Always allow")])

    def test_card_that_stays_is_reported_and_deny_is_never_pressed(self):
        card = self.show(accepts=())
        with self.assertRaisesRegex(RuntimeError, "not accepted"):
            self.ui.approve(self.pane.key, card)
        self.assertEqual(self.pressed, [("invoke", "Always allow"), ("click", "Always allow")])

    def test_nothing_is_pressed_while_a_stop_is_pending(self):
        card = self.show()
        self.worker._stop_event.set()
        with self.assertRaisesRegex(RuntimeError, "Pending command"):
            self.ui.approve(self.pane.key, card)
        self.assertEqual(self.pressed, [])

    def test_resume_never_types_into_a_chat_with_a_card(self):
        self.show()
        self.ui.type_into = lambda n, text: self.pressed.append(("type", text))
        self.assertEqual(self.ui.resume(self.pane.key), "busy")
        self.assertEqual(self.pressed, [])


class FastUI:
    """The cheap search, the full read and the approval, recorded in order."""

    def __init__(self, root):
        self.root, self.waiting, self.calls = root, True, []
        self.failure = None

    def permission_waiting(self):
        self.calls.append("search")
        return self.waiting

    def snapshot(self):
        self.calls.append("snapshot")
        return self.root

    def approve(self, key, card):
        self.calls.append(("approve", key))
        if self.failure:
            raise RuntimeError(self.failure)
        return "always"


class FastCheckTests(unittest.TestCase):
    """Between scans a waiting card is approved within seconds; the cheap search runs
    only while the option is on, and at most every few seconds."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG, auto_permissions=True))
        self.logged = []
        self.worker.log = lambda key, level="info", **kw: self.logged.append(self.worker.t(key, **kw))
        self.worker.state = self.worker.MONITORING
        self.pane = pane("Alpha")
        permission_card(self.pane)
        self.ui = FastUI(self.pane.root)
        self.engine = self.worker.engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())
        self.clock = 1000.0
        patcher = patch("session_automation.time.monotonic", lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine.next_scan = self.clock + 3600       # no regular scan in these tests

    def test_card_is_approved_between_scans(self):
        self.engine.tick(NOW)
        self.assertEqual(self.ui.calls, ["search", "snapshot", ("approve", "code:Alpha")])
        self.assertEqual(self.logged, ["Alpha: Clicked Always allow — " + FETCH])

    def test_no_card_costs_only_the_cheap_search(self):
        self.ui.waiting = False
        self.engine.tick(NOW)
        self.assertEqual(self.ui.calls, ["search"])

    def test_searches_at_most_every_few_seconds(self):
        self.ui.waiting = False
        self.engine.tick(NOW)
        self.clock += session_automation.PERMISSION_CHECK_S - 0.5
        self.engine.tick(NOW)
        self.clock += 0.5
        self.engine.tick(NOW)
        self.assertEqual(self.ui.calls, ["search", "search"])

    def test_off_or_without_automatic_sending_nothing_is_searched(self):
        for cfg in (dict(auto_permissions=False), dict(auto_send=False)):
            with self.subTest(**cfg):
                self.worker.cfg.update(dict(app.DEFAULT_CONFIG, auto_permissions=True), **cfg)
                self.engine.tick(NOW)
                self.assertEqual(self.ui.calls, [])

    def test_only_watched_conversations(self):
        self.worker.cfg.update(watch_scope="selected", selected_chats=["code:Beta"])
        self.engine.tick(NOW)
        self.assertEqual(self.ui.calls, ["search", "snapshot"])

    def test_failed_approval_waits_before_trying_again(self):
        self.ui.failure = "Permission click was not accepted"
        self.engine.tick(NOW)
        self.assertEqual(self.logged, ["Alpha: Permission click was not accepted"])
        self.clock += session_automation.PERMISSION_CHECK_S
        self.engine.tick(NOW)
        self.assertEqual(self.ui.calls.count("search"), 1)
        self.clock += session_automation.PERMISSION_BACKOFF_S
        self.engine.tick(NOW)
        self.assertEqual(self.ui.calls.count("search"), 2)

    def test_nothing_before_the_start_countdown_ends(self):
        self.worker.state = self.worker.STARTING
        self.worker.start_deadline = float("inf")
        self.worker.cfg["keep_awake"] = False
        self.worker._tick()
        self.assertEqual(self.ui.calls, [])

    def test_the_claude_window_offers_the_option_with_help(self):
        self.assertIn("auto_permissions", app.App.FEATURES)
        self.assertEqual(app.tr("en", "auto_permissions"), "Approve permission prompts automatically")
        self.assertEqual(app.tr("pl", "auto_permissions"), "Automatycznie zatwierdzaj prośby o uprawnienia")
        for lang in ("en", "pl"):
            help_text = app.tr(lang, "auto_permissions_help")
            for label in ("Always allow", "Allow once", "Deny"):
                self.assertIn(label, help_text)

    def test_the_chatgpt_window_does_not(self):
        import chatgpt_auto_continue
        self.assertNotIn("auto_permissions", chatgpt_auto_continue.ChatGPTApp.FEATURES)

    def test_cheap_search_accepts_only_allow_button_names(self):
        for name in ("Always allow 2 Ctrl Shift ⏎", "Allow once 3 Ctrl ⏎", "Allow once", "always allow2CtrlShift⏎"):
            self.assertTrue(allow_name(name), name)
        for name in ("Deny 1 Esc", "Allow", "Always allowed",
                     "Tak, działaj (Recommended) Wszystko jak wyżej: Always allow > Allow once, "
                     "szybkie sprawdzanie co ~3 s, klik bez zabierania myszy. 1"):
            self.assertFalse(allow_name(name), name)


if __name__ == "__main__":
    unittest.main()
