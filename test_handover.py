# -*- coding: utf-8 -*-
"""Tests for the context reading, the threshold and the handover flow.

No window is touched: panes are built as plain Node trees (test_sessions helpers) and
the flow talks to a recording stand-in instead of the real adapter.
"""
import datetime as dt
import json
import os
import tempfile
import unittest

import queue

import claude_auto_continue as app
import handover
from codex_log import CodexLog
from session_automation import ClaudeUI, SessionState, signals, usage_meter
from test_chatgpt import Window as ChatWindow
from test_sessions import message, node, pane
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())



def banner(_texts):
    return None, None


class MeterContextTests(unittest.TestCase):
    def test_tokens_window_and_percent(self):
        self.assertEqual(
            handover.context_from_meter(
                "Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit, Resets in 3 hr 35 min"),
            dict(used=195_800, window=1_000_000, pct=20))

    def test_percent_is_computed_when_the_meter_omits_it(self):
        self.assertEqual(handover.context_from_meter("Usage: Context 500k / 1M"),
                         dict(used=500_000, window=1_000_000, pct=50))

    def test_empty_session_reads_as_zero(self):
        self.assertEqual(
            handover.context_from_meter("Usage: Context 0, Weekly · all models: 51%, Resets Mon 6:00 PM"),
            dict(used=0, window=None, pct=None))

    def test_older_percent_only_meter(self):
        self.assertEqual(handover.context_from_meter("Usage: context 20%, plan 45%"),
                         dict(used=None, window=None, pct=20))

    def test_meter_without_context_gives_nothing(self):
        self.assertIsNone(handover.context_from_meter("Usage, Weekly · all models: 19%"))
        self.assertIsNone(handover.context_from_meter(""))
        self.assertIsNone(handover.context_from_meter(None))

    def test_plan_percentages_ignore_the_context_part(self):
        p = pane()
        node("Usage: Context 950k / 1M (95%), 12% of 5-hour limit", "ButtonControl", p.root)
        self.assertEqual(usage_meter(p, lambda _t: None)["pct"], 12)

    def test_plan_percentages_ignore_an_older_context_percent(self):
        p = pane()
        node("Usage: context 95%, 12% of 5-hour limit", "ButtonControl", p.root)
        self.assertEqual(usage_meter(p, lambda _t: None)["pct"], 12)

    def test_signals_carry_context_and_the_last_reply(self):
        p = pane()
        node("Usage: Context 712k / 1M (71%), 30% of 5-hour limit", "ButtonControl", p.root)
        message(p, 1, ("HANDOVER READY 20260926-120000: C:\\p\\.handover\\HANDOVER-20260926-120000.md",
                       "TextControl"))
        observed = signals(p, banner)
        self.assertEqual(observed["context"], dict(used=712_000, window=1_000_000, pct=71))
        self.assertIn("HANDOVER READY 20260926-120000", observed["last_text"])

    def test_signals_without_a_meter_report_no_context(self):
        observed = signals(pane(), banner)
        self.assertIsNone(observed["context"])
        self.assertEqual(observed["last_text"], "")

    def test_our_own_message_is_not_taken_for_a_reply(self):
        p = pane()
        message(p, 1, ("You said: write a handover", "TextControl"))
        self.assertEqual(signals(p, banner)["last_text"], "")



class CodexContextTests(unittest.TestCase):
    SID = "01a0a32e-fb69-76d1-b01f-0398c2aa611a"

    def build(self, thread="Praca nad planem", used=180_000, window=258_400, info=True,
              name_id=None):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, "session_index.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"id":"%s","thread_name":"%s","updated_at":"2026-09-26T10:00:00.0Z"}\n'
                    % (self.SID, thread))
        folder = os.path.join(home, "sessions", "2026", "09", "26")
        os.makedirs(folder)
        body = ('{"last_token_usage":{"input_tokens":%d,"output_tokens":0,"total_tokens":%d},'
                '"model_context_window":%d}' % (used, used, window)) if info else "null"
        line = ('{"timestamp":"2026-09-26T10:05:00.000Z","type":"event_msg","payload":'
                '{"type":"token_count","info":%s}}' % body)
        with open(os.path.join(folder, "rollout-2026-09-26T10-00-00-%s.jsonl"
                               % (name_id or self.SID)), "w", encoding="utf-8") as f:
            f.write(line + "\n")
        return CodexLog(os.path.join(home, "sessions"))

    def test_context_of_a_named_conversation(self):
        self.assertEqual(self.build().context_for("Praca nad planem"),
                         dict(used=180_000, window=258_400, pct=70))

    def test_unknown_title_gives_nothing(self):
        self.assertIsNone(self.build().context_for("Inna rozmowa"))

    def test_record_without_info_is_skipped(self):
        self.assertIsNone(self.build(info=False).context_for("Praca nad planem"))

    def test_missing_session_file_gives_nothing(self):
        self.assertIsNone(self.build(name_id="0000").context_for("Praca nad planem"))

    def test_no_index_at_all_never_raises(self):
        self.assertIsNone(CodexLog(tempfile.mkdtemp()).context_for("Praca nad planem"))

    def test_newest_entry_wins_for_a_repeated_title(self):
        log = self.build()
        index = os.path.join(os.path.dirname(log.root), "session_index.jsonl")
        with open(index, "a", encoding="utf-8") as f:      # older duplicate, other id
            f.write('{"id":"deadbeef","thread_name":"Praca nad planem",'
                    '"updated_at":"2020-01-01T10:00:00.0Z"}\n')
        self.assertEqual(log.context_for("Praca nad planem")["used"], 180_000)


class ChatGPTSignalTests(unittest.TestCase):
    """ChatGPT's own signals now carry the last reply and an empty context slot."""

    def pane_of(self, window):
        from chatgpt_automation import discover
        panes, _ = discover(window.root)
        self.assertEqual(len(panes), 1)
        return panes[0]

    def test_the_last_reply_is_read(self):
        import chatgpt_automation as gpt
        marker = r"HANDOVER READY 20260926-120000: C:\p\.handover\HANDOVER-20260926-120000.md"
        window = ChatWindow(lang="en").said("user", "carry on").said("bot", marker)
        observed = gpt.signals(self.pane_of(window))
        self.assertIn("HANDOVER READY 20260926-120000", observed["last_text"])
        self.assertIsNone(observed["context"])

    def test_our_own_turn_is_not_a_reply(self):
        import chatgpt_automation as gpt
        window = ChatWindow(lang="en").said("bot", "done").said(
            "user", "write a handover and end with HANDOVER READY 20260926-120000")
        self.assertEqual(gpt.signals(self.pane_of(window))["last_text"], "")


class ThresholdTests(unittest.TestCase):
    def test_all_accepted_spellings(self):
        self.assertEqual(handover.parse_threshold("700k"), ("tokens", 700_000))
        self.assertEqual(handover.parse_threshold("0.7M"), ("tokens", 700_000))
        self.assertEqual(handover.parse_threshold("700000"), ("tokens", 700_000))
        self.assertEqual(handover.parse_threshold(" 70 % "), ("pct", 70))
        self.assertEqual(handover.parse_threshold("0,7M"), ("tokens", 700_000))

    def test_unreadable_text(self):
        for text in ("", "dużo", "70%%", "k", None, "70 percent", "-5k"):
            self.assertIsNone(handover.parse_threshold(text), text)

    def test_clamped_to_a_safe_range(self):
        self.assertEqual(handover.parse_threshold("5k"), ("tokens", 20_000))
        self.assertEqual(handover.parse_threshold("1%"), ("pct", 20))
        self.assertEqual(handover.parse_threshold("99%"), ("pct", 95))

    def test_comparison_needs_comparable_numbers(self):
        tokens, pct = ("tokens", 700_000), ("pct", 70)
        big = dict(used=712_000, window=1_000_000, pct=71)
        small = dict(used=300_000, window=1_000_000, pct=30)
        self.assertTrue(handover.over_threshold(big, tokens))
        self.assertFalse(handover.over_threshold(small, tokens))
        self.assertTrue(handover.over_threshold(big, pct))
        self.assertFalse(handover.over_threshold(small, pct))
        self.assertTrue(handover.over_threshold(dict(used=None, window=None, pct=71), pct))
        self.assertFalse(handover.over_threshold(dict(used=None, window=None, pct=71), tokens))
        # Codex reports no percentage of its own: it is worked out from the numbers.
        self.assertTrue(handover.over_threshold(dict(used=200_000, window=258_400, pct=None), pct))
        self.assertFalse(handover.over_threshold(dict(used=180_000, window=258_400, pct=None), pct))
        self.assertFalse(handover.over_threshold(dict(used=0, window=None, pct=None), tokens))
        self.assertFalse(handover.over_threshold(None, pct))
        self.assertFalse(handover.over_threshold(big, None))

    def test_config_defaults_and_clamping(self):
        import chatgpt_auto_continue as gpt
        self.assertFalse(app.DEFAULT_CONFIG["handover_enabled"])
        self.assertEqual(app.DEFAULT_CONFIG["handover_threshold"], "700k")
        self.assertEqual(app.DEFAULT_CONFIG["plan_folder"], "")
        self.assertEqual(app.DEFAULT_CONFIG["handover_request_text"], "")
        self.assertEqual(app.DEFAULT_CONFIG["handover_continue_text"], "")
        self.assertEqual(app.DEFAULT_CONFIG["handover_timeout_min"], 30)
        self.assertEqual(gpt.DEFAULT_CONFIG["handover_threshold"], "70%")
        self.assertEqual(app.load_config(os.devnull)["handover_threshold"], "700k")
        self.assertEqual(app.load_config(os.devnull, gpt.DEFAULT_CONFIG)["handover_threshold"], "70%")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"handover_timeout_min": 9999}, f)
        self.assertEqual(app.load_config(f.name)["handover_timeout_min"], 240)


class SentinelTests(unittest.TestCase):
    ID = "20260926-120000"

    def setUp(self):
        self.project = tempfile.mkdtemp()
        self.folder = os.path.join(self.project, ".handover")
        os.makedirs(self.folder)
        self.path = os.path.join(self.folder, "HANDOVER-%s.md" % self.ID)
        self.since = dt.datetime.now() - dt.timedelta(minutes=1)

    def write(self, path=None, text="stan pracy"):
        with open(path or self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_plain_marker_line(self):
        text = "Gotowe. HANDOVER READY %s: %s" % (self.ID, self.path)
        self.assertEqual(handover.sentinel_path(text, self.ID), self.path)

    def test_marker_with_formatting_around_the_path(self):
        text = "HANDOVER READY %s : `%s`" % (self.ID, self.path)
        self.assertEqual(handover.sentinel_path(text, self.ID), self.path)

    def test_marker_of_another_handover_is_refused(self):
        text = "HANDOVER READY 20200101-000000: %s" % self.path
        self.assertIsNone(handover.sentinel_path(text, self.ID))

    def test_no_marker_at_all(self):
        self.assertIsNone(handover.sentinel_path("prawie gotowe", self.ID))
        self.assertIsNone(handover.sentinel_path("", self.ID))
        self.assertIsNone(handover.sentinel_path(None, self.ID))

    def test_our_own_instruction_does_not_look_like_a_marker(self):
        instruction = handover.request_message(dict(app.DEFAULT_CONFIG), lambda k, **kw: app.tr("pl", k, **kw),
                                               "712k / 1M", self.ID)
        self.assertIsNone(handover.sentinel_path(instruction, self.ID))

    def test_any_marker_is_recognised_for_the_second_handover_guard(self):
        old = r"HANDOVER READY 20200101-000000: c:\proj\.handover\HANDOVER-20200101-000000.md"
        self.assertTrue(handover.sentinel_any(old))
        self.assertFalse(handover.sentinel_any("nothing here"))

    def test_file_must_exist_be_fresh_and_named_as_asked(self):
        self.assertFalse(handover.file_ok(self.path, self.ID, self.since))
        self.write(text="")
        self.assertFalse(handover.file_ok(self.path, self.ID, self.since))
        self.write()
        self.assertTrue(handover.file_ok(self.path, self.ID, self.since))
        self.assertFalse(handover.file_ok(self.path, self.ID, dt.datetime.now() + dt.timedelta(hours=2)))

    def test_file_in_the_wrong_place_or_with_the_wrong_name(self):
        other = os.path.join(self.folder, "notes.md")
        self.write(other)
        self.assertFalse(handover.file_ok(other, self.ID, self.since))
        loose = os.path.join(self.project, "HANDOVER-%s.md" % self.ID)
        self.write(loose)
        self.assertFalse(handover.file_ok(loose, self.ID, self.since))

    def test_project_root_is_the_folder_holding_dot_handover(self):
        self.assertEqual(handover.project_root(self.path), self.project)
        self.assertIsNone(handover.project_root(os.path.join(self.project, "notes", "HANDOVER-x.md")))
        self.assertIsNone(handover.project_root(""))

    def test_id_is_a_timestamp(self):
        self.assertEqual(handover.new_id(dt.datetime(2026, 9, 26, 12, 0, 0)), "20260926-120000")


class MessageTests(unittest.TestCase):
    def t(self, lang="pl"):
        return lambda key, **kw: app.tr(lang, key, **kw)

    def test_request_keeps_the_technical_tail_after_an_edited_body(self):
        cfg = dict(app.DEFAULT_CONFIG, handover_request_text="Zrób handover po swojemu.")
        text = handover.request_message(cfg, self.t(), "712k / 1M", "20260926-120000")
        self.assertTrue(text.startswith("Zrób handover po swojemu."))
        self.assertIn(".handover/HANDOVER-20260926-120000.md", text)
        self.assertIn("HANDOVER READY 20260926-120000", text)
        self.assertNotIn("\n", text)

    def test_request_default_body_names_the_context(self):
        text = handover.request_message(dict(app.DEFAULT_CONFIG), self.t(), "712k / 1M", "x")
        self.assertIn("712k / 1M", text)

    def test_continue_with_and_without_a_plan_folder(self):
        cfg = dict(app.DEFAULT_CONFIG)
        path = os.path.join(r"C:\p", ".handover", "H.md")
        with_plan = handover.continue_message(cfg, self.t(), path, r"C:\p\docs\plan")
        self.assertIn(r"C:\p\docs\plan", with_plan)
        self.assertIn("graphify", with_plan)
        self.assertIn(path, with_plan)
        without = handover.continue_message(cfg, self.t(), path, "")
        self.assertNotIn("docs", without)
        self.assertIn(path, without)
        self.assertNotIn("\n", without)

    def test_both_languages_have_every_handover_text(self):
        keys = ("handover_request_default", "handover_request_tail", "handover_request_again",
                "handover_continue_default", "handover_continue_tail", "handover_continue_plan",
                "handover_continue_noplan", "handover_stop", "handover_wait", "handover_new",
                "handed", "handover_enabled", "handover_enabled_help", "lbl_threshold",
                "hint_threshold", "lbl_plan", "btn_plan_pick", "btn_handover_texts",
                "log_handover_start", "log_handover_file", "log_handover_new_chat",
                "handover_no_marker", "handover_no_file", "handover_no_project",
                "handover_timeout", "handover_stop_failed")
        for lang in ("en", "pl"):
            for key in keys:
                self.assertNotEqual(app.tr(lang, key), key, "%s missing in %s" % (key, lang))

    def test_context_label_reads_as_in_the_window(self):
        self.assertEqual(handover.context_label(dict(used=712_000, window=1_000_000, pct=71)),
                         "712k / 1M (71%)")
        self.assertEqual(handover.context_label(dict(used=180_000, window=258_400, pct=70)),
                         "180k / 258.4k (70%)")
        self.assertEqual(handover.context_label(dict(used=None, window=None, pct=71)), "71%")
        self.assertEqual(handover.context_label(None), "—")


class FlowUI:
    """Adapter stand-in: records what the flow asked the window to do."""

    def __init__(self, new_key="code:Nowa rozmowa"):
        self.calls, self.new_key = [], new_key
        self.stopped, self.fail_new, self.fail_send = True, None, None

    def stop(self, pane):
        self.calls.append(("stop", pane.key))
        return self.stopped

    def send(self, pane, message):
        self.calls.append(("send", pane.key, message))
        if self.fail_send:
            raise RuntimeError(self.fail_send)

    def new_chat(self, key, project, message):
        self.calls.append(("new_chat", key, project, message))
        if self.fail_new:
            raise handover.HandoverRefused(self.fail_new)
        return self.new_key


class FlowTests(unittest.TestCase):
    KEY = "code:Alpha"

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(
            app.DEFAULT_CONFIG, handover_enabled=True, handover_threshold="700k"))
        self.worker.log = lambda *a, **kw: None
        self.engine = self.worker.engine
        self.ui = FlowUI()
        self.engine.ui = self.ui
        self.flow = self.engine.handover
        self.pane = pane("Alpha")
        self.session = SessionState()
        # Close to the real clock: the handover file's freshness is checked against it.
        self.now = dt.datetime.now().replace(microsecond=0)
        self.project = tempfile.mkdtemp()

    # --- helpers ------------------------------------------------------------
    def observed(self, **kw):
        base = dict(limit=False, reset=None, error=None, permanent=False, retry=None,
                    question=None, permission=None, busy=False, last_text="",
                    context=dict(used=712_000, window=1_000_000, pct=71))
        return dict(base, **kw)

    def step(self, at=None, blocked=False, **kw):
        self.flow.step(self.KEY, self.session, self.observed(**kw), at or self.now,
                       self.pane, blocked)

    def state(self):
        return self.flow.state_for(self.KEY)

    def write_handover(self, handover_id):
        folder = os.path.join(self.project, ".handover")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "HANDOVER-%s.md" % handover_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write("stan pracy")
        return path

    def marker(self, handover_id, path):
        return "Zrobione. HANDOVER READY %s: %s" % (handover_id, path)

    def sends(self):
        return [call for call in self.ui.calls if call[0] == "send"]

    # --- starting -----------------------------------------------------------
    def test_busy_chat_is_stopped_first(self):
        self.step(busy=True)
        self.assertEqual(self.ui.calls, [("stop", self.KEY)])
        self.assertEqual(self.state().phase, "stopping")
        self.assertEqual(self.session.phase, "handover_stop")

    def test_idle_chat_gets_the_request(self):
        self.step()
        self.assertEqual(len(self.sends()), 1)
        self.assertIn(".handover/HANDOVER-", self.sends()[0][2])
        self.assertIn("712k / 1M (71%)", self.sends()[0][2])
        self.assertEqual(self.state().phase, "requested")
        self.assertEqual(self.session.phase, "handover_wait")
        self.assertTrue(self.state().id)

    def test_a_stopped_chat_gets_the_request_on_the_next_scan(self):
        self.step(busy=True)
        self.step()
        self.assertEqual([c[0] for c in self.ui.calls], ["stop", "send"])
        self.assertEqual(self.state().phase, "requested")

    def test_below_threshold_does_nothing(self):
        self.step(context=dict(used=300_000, window=1_000_000, pct=30))
        self.assertEqual(self.ui.calls, [])
        self.assertEqual(self.state().phase, "idle")

    def test_unknown_context_does_nothing(self):
        self.step(context=None)
        self.assertEqual(self.ui.calls, [])

    def test_option_off_does_nothing(self):
        for setting in (dict(handover_enabled=False), dict(auto_send=False)):
            with self.subTest(**setting):
                self.worker.cfg.update(dict(app.DEFAULT_CONFIG, handover_enabled=True), **setting)
                self.step()
                self.assertEqual(self.ui.calls, [])

    def test_question_permission_or_limit_block_the_start(self):
        for blocker in (dict(question={"fingerprint": "q"}), dict(permission={"title": "x"}),
                        dict(limit=True)):
            with self.subTest(**blocker):
                self.step(**blocker)
                self.assertEqual(self.ui.calls, [])
                self.assertEqual(self.state().phase, "idle")

    def test_a_blocked_scheduler_never_starts_a_handover(self):
        self.step(blocked=True)
        self.assertEqual(self.ui.calls, [])

    def test_already_handed_over_chat_is_left_alone(self):
        old = r"HANDOVER READY 20200101-000000: c:\p\.handover\HANDOVER-20200101-000000.md"
        self.step(last_text=old)
        self.assertEqual(self.ui.calls, [])

    def test_only_one_handover_at_a_time(self):
        self.step()
        other = SessionState()
        self.flow.step("code:Beta", other, self.observed(), self.now, pane("Beta"), False)
        self.assertEqual(len(self.sends()), 1)

    def test_a_failed_send_is_retried_instead_of_being_lost(self):
        self.ui.fail_send = "Existing draft; leaving it untouched"
        with self.assertRaises(RuntimeError):
            self.step()
        self.assertEqual(self.state().phase, "idle")
        self.ui.fail_send = None
        self.step()
        self.assertEqual(self.state().phase, "requested")

    # --- waiting for the handover ------------------------------------------
    def test_marker_and_file_open_the_new_chat(self):
        self.step()
        handover_id = self.state().id
        path = self.write_handover(handover_id)
        self.step(last_text=self.marker(handover_id, path))
        new = [c for c in self.ui.calls if c[0] == "new_chat"]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0][2], self.project)
        self.assertIn(path, new[0][3])
        self.assertEqual(self.state().phase, "handed")
        self.assertEqual(self.state().new_key, self.ui.new_key)
        self.assertEqual(self.session.phase, "handed")
        self.assertIn(self.ui.new_key, self.engine.sessions)

    def test_a_working_chat_is_left_to_write(self):
        self.step()
        self.step(busy=True)
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.state().phase, "requested")

    def test_marker_without_a_file_reminds_once_then_needs_attention(self):
        self.step()
        handover_id = self.state().id
        self.step(last_text=self.marker(handover_id, os.path.join(self.project, ".handover",
                                                                 "HANDOVER-%s.md" % handover_id)))
        self.assertEqual(self.state().phase, "reminded")
        self.assertEqual(len(self.sends()), 2)
        self.step()
        self.assertEqual(self.state().phase, "attention")
        self.assertEqual(self.session.phase, "exhausted")
        self.assertIn("beep", [kind for kind, _ in list(self.worker.out.queue)])

    def test_a_reply_without_the_marker_reminds_once(self):
        self.step()
        self.step(last_text="prawie gotowe, jeszcze chwila")
        self.assertEqual(self.state().phase, "reminded")
        self.assertIn(self.state().id, self.sends()[1][2])

    def test_a_marker_of_another_handover_is_ignored(self):
        self.step()
        path = self.write_handover("20200101-000000")
        self.step(last_text=self.marker("20200101-000000", path))
        self.assertEqual(self.state().phase, "reminded")

    def test_failed_new_chat_needs_attention_and_sends_nothing_more(self):
        self.ui.fail_new = "the project of the new conversation could not be confirmed"
        self.step()
        handover_id = self.state().id
        path = self.write_handover(handover_id)
        self.step(last_text=self.marker(handover_id, path))
        self.assertEqual(self.state().phase, "attention")
        self.assertEqual(len(self.sends()), 1)
        self.step(last_text=self.marker(handover_id, path))
        self.assertEqual(len(self.ui.calls), 2)

    def test_three_failed_stops_need_attention(self):
        self.ui.stopped = True
        at = self.now
        for _ in range(4):
            self.step(at=at, busy=True)
            at += dt.timedelta(minutes=2)
        self.assertEqual(len([c for c in self.ui.calls if c[0] == "stop"]), 3)
        self.assertEqual(self.state().phase, "attention")

    def test_timeout_counts_only_unblocked_scans(self):
        self.step()
        at, scan = self.now, dt.timedelta(seconds=30)
        for _ in range(80):          # 40 minutes of waiting for a limit reset
            at += scan
            self.step(at=at, blocked=True)
        self.assertEqual(self.state().phase, "requested")
        for _ in range(70):          # 35 minutes of writing, which is over the 30 allowed
            at += scan
            self.step(at=at, busy=True)
        self.assertEqual(self.state().phase, "attention")

    # --- the limit path -----------------------------------------------------
    def test_resume_text_asks_for_the_handover_instead_of_the_users_message(self):
        text = self.flow.resume_text(self.KEY, self.session, self.observed(limit=True), self.now)
        self.assertIn(".handover/HANDOVER-", text)
        self.assertEqual(self.state().phase, "idle")      # not before it is really sent
        self.flow.resume_sent(self.KEY, self.now)
        self.assertEqual(self.state().phase, "requested")
        again = self.flow.resume_text(self.KEY, self.session, self.observed(limit=True), self.now)
        self.assertIn(self.state().id, again)
        self.assertNotIn(".handover/HANDOVER-", again)

    def test_resume_text_is_none_when_no_handover_is_wanted(self):
        self.assertIsNone(self.flow.resume_text(
            self.KEY, self.session, self.observed(context=dict(used=1, window=1_000_000, pct=0)),
            self.now))

    def test_selected_scope_follows_the_new_conversation(self):
        self.worker.cfg.update(watch_scope="selected", selected_chats=[self.KEY, "code:Beta"])
        self.step()
        handover_id = self.state().id
        path = self.write_handover(handover_id)
        self.step(last_text=self.marker(handover_id, path))
        self.assertEqual(self.worker.cfg["selected_chats"], [self.ui.new_key, "code:Beta"])
        # ...and the window's list is told, so its checks show (and keep) the new one
        events = []
        while not self.worker.out.empty():
            events.append(self.worker.out.get_nowait())
        self.assertIn(("selection", [self.ui.new_key, "code:Beta"]), events)


class AdapterTests(unittest.TestCase):
    """The three window actions the handover needs, on plain Node trees."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = ClaudeUI(self.worker)
        self.clicked, self.typed, self.pressed = [], [], []
        self.ui.click = lambda n: self.clicked.append(n.name)
        self.ui.type_into = lambda n, text: self.typed.append(text)
        self.ui.press = lambda keys, wait=0.1: self.pressed.append(keys)
        self.ui.value = lambda n: ""
        self.pane = pane("Alpha")

    # --- stop ---------------------------------------------------------------
    def test_stop_clicks_the_one_stop_button(self):
        node("Stop", "ButtonControl", self.pane.root)
        self.assertTrue(self.ui.stop(self.pane))
        self.assertEqual(self.clicked, ["Stop"])

    def test_stop_without_a_button_does_nothing(self):
        self.assertFalse(self.ui.stop(self.pane))
        self.assertEqual(self.clicked, [])

    def test_stop_is_refused_when_two_buttons_match(self):
        node("Stop", "ButtonControl", self.pane.root)
        node("Zatrzymaj", "ButtonControl", self.pane.root)
        self.assertFalse(self.ui.stop(self.pane))
        self.assertEqual(self.clicked, [])

    # --- send ---------------------------------------------------------------
    def test_send_types_one_line_and_submits(self):
        self.ui.send(self.pane, "pierwsza linia\ndruga   linia")
        self.assertEqual(self.typed, ["pierwsza linia druga linia"])
        self.assertEqual(self.pressed, ["{Enter}"])

    def test_send_refuses_an_empty_message(self):
        with self.assertRaises(RuntimeError):
            self.ui.send(self.pane, "   ")
        self.assertEqual(self.typed, [])

    # --- resume with a message of ours --------------------------------------
    def resume_pane(self, retry=True, error=None):
        p = pane("Alpha")
        if retry:
            node("Try again", "ButtonControl", p.root)
        if error:
            message(p, 1, (error, "TextControl"))
        self.ui.resolve = lambda key, navigate=True: p
        return p

    def test_a_message_of_ours_never_clicks_try_again(self):
        self.resume_pane()
        result = self.ui.resume("code:Alpha", prefer_retry=True, api_error=True,
                               message="[Auto-Resume] napisz handover")
        self.assertEqual(result, "message")
        self.assertEqual(self.clicked, [])
        self.assertEqual(self.typed, ["[Auto-Resume] napisz handover"])

    def test_without_a_message_try_again_is_still_preferred(self):
        self.resume_pane()
        self.assertEqual(self.ui.resume("code:Alpha", prefer_retry=True), "retry")
        self.assertEqual(self.clicked, ["Try again"])
        self.assertEqual(self.typed, [])

    def test_the_users_message_is_sent_when_nothing_overrides_it(self):
        self.worker.cfg["message"] = "continue"
        self.resume_pane(retry=False)
        self.assertEqual(self.ui.resume("code:Alpha"), "message")
        self.assertEqual(self.typed, ["continue"])

    def test_an_empty_message_setting_sends_the_default_message(self):
        self.worker.cfg["message"] = ""
        self.resume_pane(retry=False)
        self.assertEqual(self.ui.resume("code:Alpha"), "message")
        self.assertEqual(self.typed, [app.DEFAULT_MESSAGE])

    # --- the new-conversation screen ----------------------------------------
    def new_screen(self, project="prep it", branch="main"):
        """The screen measured on 26.09: where to run it, the project, the branch, then
        the composer."""
        root = node()
        row = node(parent=root)
        node("Local", "ButtonControl", row)
        node(project, "ButtonControl", row)
        node(branch, "ComboBoxControl", row)
        node("worktree", "CheckBoxControl", row)
        node("Add another folder", "ButtonControl", row)
        box = node(parent=row)
        node("Prompt", "EditControl", box)
        return root

    def test_the_project_button_is_the_one_before_the_branch_box(self):
        root = self.new_screen()
        prompt, project = self.ui.new_session_screen(root)
        self.assertEqual(prompt.name, "Prompt")
        self.assertEqual(project.name, "prep it")

    def test_an_ordinary_pane_is_not_a_new_conversation_screen(self):
        prompt, project = self.ui.new_session_screen(pane("Alpha").root)
        self.assertIsNone(prompt)
        self.assertIsNone(project)

    # --- new_chat -----------------------------------------------------------
    def wire_new_chat(self, project="prep it", picks=True):
        """Old pane + sidebar New + the new-conversation screen, all fake."""
        old = pane("Alpha")
        sidebar = node("Sidebar", parent=old.root)
        node("New", "ButtonControl", sidebar)
        screen = self.new_screen(project=project)
        self.ui.resolve = lambda key, navigate=True: old
        self.ui.snapshot = lambda: old.root
        self.ui.panes = lambda root=None: [old]
        self.ui.new_session_screen = lambda root: (
            node("Prompt", "EditControl", screen), node(project, "ButtonControl", screen))
        self.picked = []

        def pick(button, wanted):
            self.picked.append((button.name, wanted))
            if picks:
                self.ui.new_session_screen = lambda root: (
                    node("Prompt", "EditControl", screen),
                    node(os.path.basename(self.project), "ButtonControl", screen))
            return picks
        self.ui.pick_project = pick
        self.ui.settled_key = lambda before, wait_s=None: "code:Nowa rozmowa"
        self.ui.send = lambda pane_, text: self.typed.append(text)
        self.project = os.path.join(tempfile.mkdtemp(), "auto-resume")
        os.makedirs(self.project)
        return old

    def test_new_chat_picks_the_project_and_sends(self):
        self.wire_new_chat()
        key = self.ui.new_chat("code:Alpha", self.project, "przejmij pracę")
        self.assertEqual(key, "code:Nowa rozmowa")
        self.assertEqual(self.picked, [("prep it", "auto-resume")])
        self.assertEqual(self.typed, ["przejmij pracę"])


    def test_a_folded_sidebar_is_unfolded_before_giving_up(self):
        """A narrow window hides the sidebar, and with it the button that starts a
        conversation (seen live on 26.09)."""
        old = pane("Alpha")
        sidebar = node("Sidebar", parent=old.root)
        toggle = node("Show sidebar", "ButtonControl", old.root)
        self.ui.resolve = lambda key, navigate=True: old
        self.ui.snapshot = lambda: old.root
        self.ui.panes = lambda root=None: [old]
        self.project = os.path.join(tempfile.mkdtemp(), "auto-resume")
        os.makedirs(self.project)
        screen = self.new_screen(project=os.path.basename(self.project))
        self.ui.new_session_screen = lambda root: (
            node("Prompt", "EditControl", screen),
            node(os.path.basename(self.project), "ButtonControl", screen))
        self.ui.settled_key = lambda before, wait_s=None: "code:Nowa rozmowa"
        self.ui.send = lambda pane_, text: self.typed.append(text)

        def unfold(n):
            self.clicked.append(n.name)
            if n is toggle:                    # the sidebar's button appears with it
                node("New", "ButtonControl", sidebar)
        self.ui.click = unfold
        key = self.ui.new_chat("code:Alpha", self.project, "przejmij pracę")
        self.assertEqual(key, "code:Nowa rozmowa")
        self.assertIn("Show sidebar", self.clicked)
        self.assertIn("New", self.clicked)

    def test_new_chat_sends_nothing_when_the_project_cannot_be_chosen(self):
        self.wire_new_chat(picks=False)
        with self.assertRaises(handover.HandoverRefused):
            self.ui.new_chat("code:Alpha", self.project, "przejmij pracę")
        self.assertEqual(self.typed, [])

    def test_new_chat_needs_a_project_folder(self):
        self.wire_new_chat()
        with self.assertRaises(handover.HandoverRefused):
            self.ui.new_chat("code:Alpha", "", "przejmij pracę")

    def test_new_chat_leaves_a_draft_alone(self):
        self.wire_new_chat()
        self.ui.value = lambda n: "szkic użytkownika"
        with self.assertRaises(RuntimeError):
            self.ui.new_chat("code:Alpha", self.project, "przejmij pracę")
        self.assertEqual(self.typed, [])


class ChatGPTAdapterTests(unittest.TestCase):
    def setUp(self):
        import chatgpt_auto_continue as gpt
        self.worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = self.worker.engine.ui

    def test_the_project_name_is_read_from_the_button(self):
        from session_automation import Node
        for name, expected in (("Zmień projekt: auto-resume", "auto-resume"),
                               ("Change project: auto-resume", "auto-resume"),
                               ("Pracuj bez projektu", "")):
            self.assertEqual(self.ui.project_name(Node(name, "ButtonControl")), expected)

    def test_stop_uses_the_composer_button(self):
        window = ChatWindow(lang="en", submit="Stop")
        pane_ = self.ui.panes(window.root)[0]
        clicked = []
        self.ui.click = lambda n: clicked.append(n.name)
        self.assertTrue(self.ui.stop(pane_))
        self.assertEqual(clicked, ["Stop"])

    def test_stop_does_nothing_when_the_run_has_finished(self):
        window = ChatWindow(lang="en")
        pane_ = self.ui.panes(window.root)[0]
        self.ui.click = lambda n: self.fail("clicked %s" % n.name)
        self.assertFalse(self.ui.stop(pane_))


class SettingsTests(unittest.TestCase):
    """Both features are switchable in both programs, in both languages."""

    def test_both_apps_offer_the_two_options(self):
        import chatgpt_auto_continue as gpt
        for features in (app.App.FEATURES, gpt.ChatGPTApp.FEATURES):
            self.assertIn("pause_on_foreign_input", features)
            self.assertIn("handover_enabled", features)

    def test_every_option_has_an_explanation_in_both_languages(self):
        import chatgpt_auto_continue as gpt
        for key in app.App.FEATURES:
            for lang in ("en", "pl"):
                self.assertNotEqual(app.tr(lang, key + "_help"), key + "_help")
                self.assertNotEqual(gpt.app.tr(lang, key + "_help", table=gpt.STRINGS),
                                    key + "_help")

    def test_chatgpt_keeps_its_own_threshold_default(self):
        import chatgpt_auto_continue as gpt
        self.assertEqual(gpt.DEFAULT_CONFIG["handover_threshold"], "70%")
        self.assertEqual(handover.parse_threshold(gpt.DEFAULT_CONFIG["handover_threshold"]),
                         ("pct", 70))


if __name__ == "__main__":
    unittest.main()
