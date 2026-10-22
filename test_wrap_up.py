"""Claude Code's wrap-up allowance (2.1.277+): when the limit runs out in the middle of a
response, the response goes on to a safe point and ends normally. No "Session limit
reached" card ends the transcript; a strip says "Wrapped up past your usage limit" and
when the limit resets (seen 14.10). The conversation still has to be resumed after the
reset, with a text of its own."""
import datetime as dt
import queue
import types
import unittest
from unittest.mock import Mock, patch

import claude_auto_continue as app
from session_automation import SessionEngine, SessionState, discover, signals
from test_sessions import LIMIT_CARD, NOW, FakeQuota, FixtureUI, message, node, observed, pane
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


MINUTE = dt.timedelta(minutes=1)


def strip(p, *texts):
    """The notice above the composer, outside the transcript."""
    bar = node(parent=p.root)
    for text in texts:
        kind = "ButtonControl" if text in ("Learn more", "Upgrade", "Dismiss") else "TextControl"
        node(text, kind, bar)
    return bar


WRAPPED = ("Wrapped up past your usage limit", "Resets at 2:30 PM", "Learn more", "Upgrade")


class WrapUpSignalTests(unittest.TestCase):
    def test_wrapped_up_strip_is_a_limit_with_its_reset(self):
        p = pane()
        message(p, 1, ("Done: seven items. Left: the changelog.", "TextControl"))
        strip(p, *WRAPPED)
        state = signals(p, app.detect_limit_banner)
        self.assertTrue(state["limit"])
        self.assertTrue(state["wrap_up"])
        self.assertFalse(state["card"])
        self.assertEqual((state["reset"].hour, state["reset"].minute), (14, 30))

    def test_wrapping_up_while_claude_still_works(self):
        p = pane()
        strip(p, "Usage limit reached", "·", "wrapping up")
        node("Stop", "ButtonControl", p.root)
        state = signals(p, app.detect_limit_banner)
        self.assertTrue(state["limit"])
        self.assertTrue(state["wrap_up"])
        self.assertTrue(state["busy"])

    def test_plain_usage_limit_strip_is_no_wrap_up(self):
        p = pane()
        strip(p, "Usage limit reached", "Resets at 2:40 PM", "Upgrade")
        state = signals(p, app.detect_limit_banner)
        self.assertTrue(state["limit"])
        self.assertFalse(state["wrap_up"])

    def test_a_conversation_about_wrapping_up_is_no_limit(self):
        p = pane("Wrapping up the release")
        node("Wrapping up the release", "TextControl", p.root)
        state = signals(p, app.detect_limit_banner)
        self.assertFalse(state["limit"])
        self.assertFalse(state["wrap_up"])

    def test_hard_card_ending_the_transcript_is_reported(self):
        p = pane()
        message(p, 1, *LIMIT_CARD)
        strip(p, *WRAPPED)
        state = signals(p, app.detect_limit_banner)
        self.assertTrue(state["card"])
        self.assertTrue(state["wrap_up"])


class UsagePanelTests(unittest.TestCase):
    """Claude now calls the 5-hour limit the session limit (meter, 14.10)."""

    def rows(self, *names):
        controls = [(types.SimpleNamespace(Name=name), 1) for name in names]
        with patch.object(app.auto, "WalkControl", lambda *a, **kw: iter(controls)):
            return app.MonitorWorker._parse_usage_rows(object())

    def test_current_session_row_is_the_5_hour_limit(self):
        rows = self.rows("Plan usage limits", "Current session", "Resets in 3 hr 10 min", "67%",
                         "Weekly limits", "All models", "Resets Mon 6:00 PM", "19%")
        self.assertEqual(rows["5h"]["pct"], 67)
        self.assertIn("reset", rows["5h"])

    def test_session_limit_row_is_the_5_hour_limit(self):
        rows = self.rows("Session limit", "Resets at 2:30 PM", "100%", "Weekly · all models", "19%")
        self.assertEqual(rows["5h"]["pct"], 100)
        self.assertEqual(rows["weekly"]["pct"], 19)

    def test_5_hour_label_still_works(self):
        rows = self.rows("5-hour limit", "Resets in 2 hr", "45%", "Weekly · all models", "19%")
        self.assertEqual(rows["5h"]["pct"], 45)
        self.assertEqual(rows["weekly"]["pct"], 19)


class WrapUpScheduleTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = FixtureUI()
        self.engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())
        self.reset = NOW + dt.timedelta(hours=2)
        self.due = self.reset + dt.timedelta(seconds=self.worker.cfg["send_delay_after_reset_s"])

    def wrapped(self, **kwargs):
        return observed(**dict(dict(limit=True, wrap_up=True, card=False, reset=self.reset), **kwargs))

    def wrapped_up_and_resumed(self, state):
        self.engine.step("code:A", state, self.wrapped(busy=True), NOW)
        self.engine.step("code:A", state, self.wrapped(), NOW + MINUTE)
        self.engine.step("code:A", state, self.wrapped(), self.due)

    def test_wrapping_up_waits_for_the_reset(self):
        state = SessionState()
        self.engine.step("code:A", state, self.wrapped(busy=True), NOW)
        self.assertEqual((state.phase, state.reason, state.due), ("waiting", "limit", self.due))
        self.assertTrue(state.wrap_up)
        self.assertEqual(state.notice, "waiting_wrapup_at")
        self.assertEqual(self.ui.calls, [])

    def test_after_the_reset_the_wrap_up_text_is_sent(self):
        self.worker.cfg["wrapup_message"] = "Carry on with what was left."
        state = SessionState()
        self.wrapped_up_and_resumed(state)
        self.assertEqual(self.ui.messages, ["Carry on with what was left."])
        self.assertEqual((state.phase, state.notice), ("verifying", "wrapup_message"))

    def test_empty_wrap_up_text_sends_the_default(self):
        self.worker.cfg["wrapup_message"] = "  "
        state = SessionState()
        self.wrapped_up_and_resumed(state)
        self.assertEqual(self.ui.messages, [app.DEFAULT_WRAPUP_MESSAGE])

    def test_closed_strip_keeps_the_wrap_up(self):
        state = SessionState()
        self.engine.step("code:A", state, self.wrapped(), NOW)
        self.engine.step("code:A", state, observed(), self.due)
        self.assertEqual(self.ui.messages, [self.worker.cfg["wrapup_message"]])

    def test_limit_read_only_from_the_meter_keeps_the_wrap_up(self):
        # The strip was closed; the maxed meter (or the usage panel) still shows the limit,
        # but says nothing about how the turn ended.
        state = SessionState()
        self.engine.step("code:A", state, self.wrapped(), NOW)
        self.engine.step("code:A", state, self.wrapped(wrap_up=False), NOW + MINUTE)
        self.engine.step("code:A", state, self.wrapped(wrap_up=False), self.due)
        self.assertEqual(self.ui.messages, [self.worker.cfg["wrapup_message"]])

    def test_a_later_hard_limit_does_not_inherit_an_old_wrap_up(self):
        # An earlier wrap-up the user resumed by hand (Claude was busy at send time).
        state = SessionState("watching", "limit", wrap_up=True)
        self.engine.step("code:A", state, observed(limit=True, reset=self.reset), NOW)
        self.engine.step("code:A", state, observed(limit=True, reset=self.reset), self.due)
        self.assertEqual(self.ui.messages, [None])

    def test_a_cut_off_turn_gets_the_usual_message(self):
        state = SessionState()
        self.engine.step("code:A", state, self.wrapped(), NOW)
        self.engine.step("code:A", state, self.wrapped(card=True), NOW + MINUTE)
        self.engine.step("code:A", state, self.wrapped(card=True), self.due)
        self.assertFalse(state.wrap_up)
        self.assertEqual(self.ui.messages, [None])      # None: the user's "Message to send"

    def test_strip_left_after_the_resume_is_not_a_new_limit(self):
        state = SessionState()
        self.wrapped_up_and_resumed(state)
        verify = self.due + dt.timedelta(seconds=self.worker.cfg["verify_delay_s"])
        self.engine.step("code:A", state, self.wrapped(), verify)
        self.assertEqual((state.phase, state.notice), ("watching", "resumed"))
        later = self.due + dt.timedelta(seconds=self.worker.cfg["retry_wait_s"])
        for now in (later, later + dt.timedelta(hours=1)):
            self.engine.step("code:A", state, self.wrapped(), now)
        self.assertEqual(len(self.ui.calls), 1)

    def test_claude_working_after_the_reset_means_someone_resumed(self):
        state = SessionState()
        self.engine.step("code:A", state, self.wrapped(), NOW)
        self.engine.step("code:A", state, self.wrapped(busy=True), self.due)
        self.engine.step("code:A", state, self.wrapped(), self.due + 10 * MINUTE)
        self.engine.step("code:A", state, self.wrapped(), self.due + 30 * MINUTE)
        self.assertEqual(self.ui.calls, [])

    def test_a_new_limit_after_the_resume_is_waited_for(self):
        state = SessionState()
        self.wrapped_up_and_resumed(state)
        later = self.reset + dt.timedelta(hours=5)
        self.engine.step("code:A", state, self.wrapped(reset=later), self.due + 2 * MINUTE)
        self.assertEqual((state.phase, state.due), ("waiting", later + (self.due - self.reset)))

    def test_handover_comes_before_the_wrap_up_text(self):
        sent = []
        self.engine.handover.resume_text = lambda key, state, observed, now: "Write a handover."
        self.engine.handover.resume_sent = lambda key, now: sent.append(key)
        state = SessionState()
        self.wrapped_up_and_resumed(state)
        self.assertEqual(self.ui.messages, ["Write a handover."])
        self.assertEqual(sent, ["code:A"])

    def test_wrap_up_text_is_not_mistaken_for_a_handover(self):
        sent = []
        self.engine.handover.resume_sent = lambda key, now: sent.append(key)
        self.wrapped_up_and_resumed(SessionState())
        self.assertEqual(sent, [])

    def test_a_full_context_after_a_wrap_up_moves_to_a_new_conversation(self):
        asked = []
        self.engine.handover.from_wrap_up = lambda key, state, observed, now: asked.append(key) or True
        self.wrapped_up_and_resumed(SessionState())
        self.assertEqual(asked, ["code:A"])
        self.assertEqual(self.ui.messages, [])

    def test_a_wrap_up_that_cannot_be_handed_over_gets_the_usual_resume(self):
        self.engine.handover.from_wrap_up = lambda key, state, observed, now: False
        self.worker.cfg["wrapup_message"] = "Carry on with what was left."
        self.wrapped_up_and_resumed(SessionState())
        self.assertEqual(self.ui.messages, ["Carry on with what was left."])

    def test_a_hard_limit_is_never_handed_over_from_a_wrap_up(self):
        asked = []
        self.engine.handover.from_wrap_up = lambda key, state, observed, now: asked.append(key) or True
        state = SessionState()
        card = observed(limit=True, card=True, reset=self.reset)
        self.engine.step("code:A", state, card, NOW)
        self.engine.step("code:A", state, card, self.due)
        self.assertEqual(asked, [])
        self.assertEqual(len(self.ui.messages), 1)

    def test_wrap_up_with_no_reset_ignores_an_old_logged_rejection(self):
        # A wrap-up logs no rejected request: the newest one in the log belongs to an
        # older limit, and its long-past reset would resume a blocked conversation.
        p = pane("Podprojekt C")
        message(p, 1, ("Done: seven items. Left: the changelog.", "TextControl"))
        strip(p, "Wrapped up past your usage limit", "Learn more", "Upgrade")
        self.ui.snapshot = lambda: p.root
        self.ui.resolve = lambda key, navigate=True: discover(p.root)[0][0]
        self.worker._get_window = Mock(return_value=None)
        self.worker._read_usage_panel = Mock(return_value=({}, False))
        engine = SessionEngine(self.worker, self.ui, quota=FakeQuota(NOW - dt.timedelta(days=2)))
        engine.tick(now=NOW)
        state = engine.sessions["code:Podprojekt C"]
        self.assertEqual((state.phase, state.reset), ("waiting", None))
        self.assertEqual(self.ui.calls, [])


class ResumeNowTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = FixtureUI()
        self.worker.engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())
        self.worker._refresh = lambda: []
        self.worker.engine.targets = lambda panes: ["code:A"]

    def test_resume_now_sends_the_wrap_up_text_and_settles_the_strip(self):
        reset = NOW - MINUTE
        self.worker.engine.sessions["code:A"] = SessionState("waiting", "limit", reset=reset, wrap_up=True)
        self.worker.command("send_now")
        self.worker._process_commands()
        self.assertEqual(self.ui.messages, [self.worker.cfg["wrapup_message"]])
        self.assertEqual(self.worker.engine.sessions["code:A"].wrap_done, reset)

    def test_resume_now_without_a_wrap_up_sends_the_usual_message(self):
        self.worker.command("send_now")
        self.worker._process_commands()
        self.assertEqual(self.ui.messages, [None])


if __name__ == "__main__":
    unittest.main()
