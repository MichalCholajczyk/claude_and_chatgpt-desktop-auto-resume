# -*- coding: utf-8 -*-
"""Tests for the input guard: who is holding the mouse and keyboard, and what the
program does about it. No real input is sent and no window is touched."""
import datetime as dt
import json
import os
import queue
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import claude_auto_continue as app
from input_guard import ClaudeComputerUse, InputGuard, InputPaused, OwnInputClock, overlay_phrases


class FakeOwn:
    """Stand-in for the shared own-input record; the test sets the age."""

    def __init__(self, age=None):
        self.age = age
        self.marks = 0

    def mark(self, ahead_ms=0):
        self.marks += 1
        self.age = 0

    def age_ms(self):
        return self.age


class FakeCU:
    def __init__(self, active=False):
        self.value, self.holds = active, []

    def active(self, hold_s):
        self.holds.append(hold_s)
        return self.value


def guard(worker, last_input_ms=10_000, own=None, cu=False, overlay=False):
    return InputGuard(worker, last_input=lambda: last_input_ms, own=own or FakeOwn(),
                      cu=FakeCU(cu), overlay=lambda skip_hwnd=None, phrases=None: overlay)


def worker():
    w = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
    w.log = lambda *a, **kw: None
    return w


class ForeignInputTests(unittest.TestCase):
    def setUp(self):
        self.worker = worker()

    def test_fresh_foreign_input_pauses(self):
        g = guard(self.worker, last_input_ms=1_000, own=FakeOwn(age=90_000))
        self.assertAlmostEqual(g.foreign_input_age_s(), 1.0)
        self.assertTrue(g.user_busy())
        self.assertEqual(g.pause_reason(), "user")

    def test_our_own_input_is_not_foreign(self):
        g = guard(self.worker, last_input_ms=1_000, own=FakeOwn(age=1_100))
        self.assertIsNone(g.foreign_input_age_s())
        self.assertFalse(g.user_busy())
        self.assertIsNone(g.pause_reason())

    def test_input_older_than_ours_is_not_foreign(self):
        g = guard(self.worker, last_input_ms=5_000, own=FakeOwn(age=1_000))
        self.assertIsNone(g.foreign_input_age_s())

    def test_quiet_period_releases(self):
        self.worker.cfg["foreign_input_quiet_s"] = 30
        g = guard(self.worker, last_input_ms=31_000, own=FakeOwn(age=90_000))
        self.assertFalse(g.user_busy())
        self.assertIsNone(g.pause_reason())

    def test_unknown_own_input_still_compares(self):
        g = guard(self.worker, last_input_ms=1_000, own=FakeOwn(age=None))
        self.assertAlmostEqual(g.foreign_input_age_s(), 1.0)

    def test_option_off_never_pauses(self):
        self.worker.cfg["pause_on_foreign_input"] = False
        g = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000), cu=True, overlay=True)
        self.assertIsNone(g.pause_reason())
        self.assertFalse(g.user_busy())

    def test_manual_command_ignores_the_user_signal_only(self):
        g = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000), cu=True)
        g.ignore_user_for(60)
        self.assertFalse(g.user_busy())
        self.assertEqual(g.pause_reason(), "claude_cu")

    def test_computer_use_and_overlay_have_their_own_reasons(self):
        calm = dict(last_input_ms=90_000, own=FakeOwn(age=90_000))
        self.assertEqual(guard(self.worker, cu=True, **calm).pause_reason(), "claude_cu")
        self.assertEqual(guard(self.worker, overlay=True, **calm).pause_reason(), "chatgpt_cu")
        self.assertIsNone(guard(self.worker, **calm).pause_reason())

    def test_the_hold_from_the_config_reaches_the_log_reader(self):
        self.worker.cfg["computer_use_hold_s"] = 240
        fake = FakeCU(False)
        g = InputGuard(self.worker, last_input=lambda: 90_000, own=FakeOwn(age=90_000),
                       cu=fake, overlay=lambda **kw: False)
        g.pause_reason()
        self.assertEqual(fake.holds, [240])

    def test_marking_own_input_clears_the_foreign_reading(self):
        own = FakeOwn(age=90_000)
        g = guard(self.worker, last_input_ms=100, own=own)
        self.assertTrue(g.user_busy())
        g.mark_own_input()
        self.assertEqual(own.marks, 1)
        self.assertIsNone(g.foreign_input_age_s())


CU_LINE = ('{"type":"assistant","timestamp":"%s","message":{"model":"claude-opus-5",'
           '"content":[{"type":"tool_use","name":"mcp__computer-use__screenshot"}],'
           '"stop_reason":"tool_use"}}')
END_LINE = ('{"type":"assistant","timestamp":"%s","message":{"content":[{"type":"text",'
            '"text":"done"}],"stop_reason":"end_turn"}}')
OTHER_LINE = ('{"type":"assistant","timestamp":"%s","message":{"content":[{"type":"tool_use",'
              '"name":"Bash"}],"stop_reason":"tool_use"}}')
# Every session with the computer-use MCP connected starts with its instructions, which
# name the tools; a file the agent reads may name them too. Neither is a call.
MCP_NOTE_LINE = ('{"type":"attachment","timestamp":"%s","attachment":{"type":"mcp_instructions",'
                 '"content":"You have a computer-use MCP available (tools named `mcp__computer-use__*`)."}}')
QUOTED_LINE = ('{"type":"user","timestamp":"%s","message":{"content":[{"type":"tool_result",'
               '"content":"the last `mcp__computer-use__screenshot` call in a turn"}]}}')


def stamp(seconds_ago):
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ClaudeComputerUseTests(unittest.TestCase):
    def logs(self, *lines):
        root = tempfile.mkdtemp()
        project = os.path.join(root, "C--project")
        os.makedirs(project)
        with open(os.path.join(project, "session.jsonl"), "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        return ClaudeComputerUse(root)

    def test_recent_tool_call_in_an_open_turn_is_active(self):
        self.assertTrue(self.logs(CU_LINE % stamp(20)).active(120))

    def test_end_turn_after_the_tool_call_releases(self):
        self.assertFalse(self.logs(CU_LINE % stamp(20), END_LINE % stamp(5)).active(120))

    def test_another_tool_after_it_keeps_the_hold(self):
        cu = self.logs(CU_LINE % stamp(30), OTHER_LINE % stamp(5))
        self.assertTrue(cu.active(120))

    def test_older_than_the_hold_releases(self):
        self.assertFalse(self.logs(CU_LINE % stamp(600)).active(120))

    def test_the_hold_runs_out_while_the_log_stays_unchanged(self):
        """An agent may run a long command right after a computer-use call. Its log then
        stays as it is, and the hold must still end on time (26.09: it lasted until the
        log changed again, 4 minutes instead of 2)."""
        cu = self.logs(CU_LINE % stamp(20))
        self.assertTrue(cu.active(120))
        with patch("input_guard.time.time", return_value=time.time() + 200):
            self.assertFalse(cu.active(120))

    def test_the_mcp_instructions_naming_the_tools_are_not_a_call(self):
        self.assertFalse(self.logs(MCP_NOTE_LINE % stamp(20)).active(120))

    def test_a_tool_result_quoting_a_tool_name_is_not_a_call(self):
        self.assertFalse(self.logs(OTHER_LINE % stamp(30), QUOTED_LINE % stamp(10)).active(120))

    def test_a_quote_after_a_real_call_keeps_the_hold(self):
        self.assertTrue(self.logs(CU_LINE % stamp(30), QUOTED_LINE % stamp(10)).active(120))

    def test_no_logs_at_all_is_not_active(self):
        self.assertFalse(ClaudeComputerUse(tempfile.mkdtemp()).active(120))

    def test_unreadable_root_never_raises(self):
        gone = os.path.join(tempfile.mkdtemp(), "gone")
        self.assertFalse(ClaudeComputerUse(gone).active(120))

    def test_stale_file_outside_the_window_is_not_read(self):
        cu = self.logs(CU_LINE % stamp(20))
        path = os.path.join(cu.root, "C--project", "session.jsonl")
        old = dt.datetime.now().timestamp() - 3600
        os.utime(path, (old, old))
        self.assertFalse(cu.active(120))


class OverlayTests(unittest.TestCase):
    def home_with(self, text):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, "computer-use"))
        with open(os.path.join(home, "computer-use", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"strings": {"usingComputer": text, "escToCancel": "Esc to cancel"}}, f)
        return home

    def test_phrases_come_from_the_codex_config(self):
        phrases = overlay_phrases(self.home_with("ChatGPT używa Twojego komputera"))
        self.assertIn("chatgpt używa twojego komputera", phrases)
        self.assertIn("is using your computer", phrases)

    def test_missing_config_still_gives_fallbacks(self):
        phrases = overlay_phrases(os.path.join(tempfile.mkdtemp(), "gone"))
        self.assertIn("is using your computer", phrases)
        self.assertIn("używa twojego komputera", phrases)


class TwoProgramsTests(unittest.TestCase):
    """Claude's and ChatGPT's Auto-Resume share the record of their own input."""

    def test_the_other_programs_click_is_not_a_person_even_before_it_returns(self):
        name = "Local\\AutoResume.Test.%d.%s" % (os.getpid(), self.id())
        clicking = InputGuard(worker(), last_input=lambda: 50, own=OwnInputClock(name),
                              cu=FakeCU(), overlay=lambda **kw: False)
        watching = InputGuard(worker(), last_input=lambda: 50, own=OwnInputClock(name),
                              cu=FakeCU(), overlay=lambda **kw: False)
        with clicking.sending():
            self.assertFalse(watching.user_busy())    # the click went out 50 ms ago
        self.assertFalse(watching.user_busy())


class GuardWiringTests(unittest.TestCase):
    def setUp(self):
        self.worker = worker()
        self.worker.state = self.worker.MONITORING
        self.engine = self.worker.engine

    def states(self):
        return [data for kind, data in list(self.worker.out.queue) if kind == "state"]

    def test_tick_skips_the_scan_and_publishes_the_reason(self):
        def never(*_a, **_kw):
            raise AssertionError("scanned while paused")
        self.engine.refresh = never
        self.engine.check_permissions = never
        self.worker.guard = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000))
        self.engine.tick()
        self.assertEqual(self.worker.pause_reason, "user")
        self.assertEqual(self.states()[-1]["state"], "PAUSED")
        self.assertEqual(self.states()[-1]["pause_reason"], "user")

    def test_the_pause_ends_and_scanning_resumes(self):
        self.worker.guard = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000))
        self.engine.refresh = lambda: (_ for _ in ()).throw(AssertionError("too early"))
        self.engine.check_permissions = lambda: None
        self.engine.tick()
        self.worker.guard = guard(self.worker, last_input_ms=90_000, own=FakeOwn(age=90_000))
        self.engine.refresh = lambda: []
        self.engine.tick()
        self.assertIsNone(self.worker.pause_reason)

    def test_defaults_and_clamping(self):
        self.assertTrue(app.DEFAULT_CONFIG["pause_on_foreign_input"])
        self.assertEqual(app.DEFAULT_CONFIG["foreign_input_quiet_s"], 30)
        self.assertEqual(app.DEFAULT_CONFIG["computer_use_hold_s"], 120)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"foreign_input_quiet_s": 9999, "computer_use_hold_s": 1}, f)
        cfg = app.load_config(f.name)
        self.assertEqual(cfg["foreign_input_quiet_s"], 300)
        self.assertEqual(cfg["computer_use_hold_s"], 30)

    def test_click_refuses_while_someone_holds_the_mouse(self):
        from session_automation import ClaudeUI
        ui = ClaudeUI(self.worker)
        self.worker.guard = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000))
        with self.assertRaises(InputPaused):
            ui.click(object())

    def test_press_refuses_and_marks_when_it_sends(self):
        from session_automation import ClaudeUI
        ui = ClaudeUI(self.worker)
        own = FakeOwn(age=90_000)
        self.worker.guard = guard(self.worker, last_input_ms=100, own=own)
        with self.assertRaises(InputPaused):
            ui.press("{Enter}")
        sent = []
        self.worker.guard = guard(self.worker, last_input_ms=90_000, own=own)
        with patch.object(app.auto, "SendKeys", lambda keys, **kw: sent.append((keys, own.marks))):
            ui.press("{Enter}")
        self.assertEqual(sent, [("{Enter}", 1)])     # marked as ours before it went out...
        self.assertEqual(own.marks, 2)               # ...and again once it had

    def test_click_is_marked_as_ours_before_it_is_sent(self):
        """The other Auto-Resume checks the shared record at any moment, also while a
        click is still on its way: it must never see a stranger at the mouse then."""
        from session_automation import ClaudeUI, Node
        own, seen = FakeOwn(age=90_000), []
        self.worker.guard = guard(self.worker, last_input_ms=90_000, own=own)
        self.worker.hwnd = 5
        self.worker._focus_window = lambda hwnd: None
        control = types.SimpleNamespace(IsEnabled=True, IsOffscreen=False,
                                        Click=lambda **kw: seen.append(own.marks))
        with patch.object(app.user32, "GetForegroundWindow", return_value=5):
            ClaudeUI(self.worker).click(Node("Stop", "ButtonControl", (0, 0, 9, 9), control))
        self.assertEqual((seen, own.marks), ([1], 2))

    def test_the_usage_panel_marks_its_clicks_before_they_are_sent(self):
        own, seen = FakeOwn(age=90_000), []
        self.worker.guard = guard(self.worker, last_input_ms=90_000, own=own)
        self.worker.hwnd = 5
        button = types.SimpleNamespace(IsOffscreen=False, IsEnabled=True,
                                       Click=lambda **kw: seen.append(("click", own.marks)))
        user32 = types.SimpleNamespace(GetForegroundWindow=lambda: 5, GetCursorPos=lambda p: 1,
                                       SetCursorPos=lambda x, y: seen.append(("cursor", own.marks)))
        with patch.object(self.worker, "_find_usage_button", return_value=button), \
                patch.object(self.worker, "_focus_window"), patch.object(self.worker, "_wake_accessibility"), \
                patch.object(self.worker, "_find_usage_panel", return_value=None), \
                patch.object(app, "user32", user32), patch.object(app.time, "sleep"), \
                patch.object(app.auto, "SendKeys", lambda keys, **kw: seen.append((keys, own.marks))):
            self.worker._read_usage_panel(object())
        # every input goes out right after a mark of ours (an odd count: before, not after)
        self.assertEqual([step for step, _ in seen], ["click", "{Esc}", "cursor"])
        self.assertTrue(all(marks % 2 == 1 for _, marks in seen), seen)

    def test_manual_send_now_does_not_wait_for_calm(self):
        ignored = []
        self.worker.guard = guard(self.worker, last_input_ms=100, own=FakeOwn(age=90_000))
        self.worker.guard.ignore_user_for = lambda seconds: ignored.append(seconds)
        self.worker._refresh = lambda: []
        self.engine.targets = lambda panes: []
        self.worker.command("send_now")
        self.worker._process_commands()
        self.assertTrue(ignored)

    def test_texts_exist_in_both_languages(self):
        for lang in ("en", "pl"):
            for key in ("state_paused", "pause_user", "pause_claude_cu", "pause_chatgpt_cu",
                        "cap_paused", "log_paused", "log_pause_over",
                        "pause_on_foreign_input", "pause_on_foreign_input_help", "lbl_quiet"):
                self.assertNotEqual(app.tr(lang, key), key, "%s missing in %s" % (key, lang))


if __name__ == "__main__":
    unittest.main()
