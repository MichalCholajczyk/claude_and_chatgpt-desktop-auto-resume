"""Tests for reading the latest usage-limit reset from Codex's local session logs."""
import datetime as dt
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import codex_log
from codex_log import CodexLog

RESET_0933 = 1789457599   # 2026-09-15 07:33:19 UTC, as written by the ChatGPT app on 15.09


def limits(timestamp, primary=100.0, secondary=31.0, resets_at=RESET_0933, limit_id="codex"):
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {
            "limit_id": limit_id,
            "primary": {"used_percent": primary, "window_minutes": 300, "resets_at": resets_at},
            "secondary": {"used_percent": secondary, "window_minutes": 10080, "resets_at": resets_at + 555621}}}})


def empty_limits(timestamp):
    """The app also logs a second, empty snapshot ("premium") right after the real one."""
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {"limit_id": "premium", "primary": None, "secondary": None}}})


def rejection(timestamp, message="You've hit your usage limit. ... or try again at 9:33 AM.",
              info="usage_limit_exceeded"):
    return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": {
        "type": "task_complete", "last_agent_message": None,
        "error": {"message": message, "codex_error_info": info}}})


class CodexLogTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, day, name, *lines):
        folder = os.path.join(self.root, "2026", "09", day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_reset_comes_from_the_used_up_window_of_the_snapshot(self):
        self.write("15", "rollout-a.jsonl", limits("2026-09-15T03:51:22.275Z"),
                   empty_limits("2026-09-15T03:51:22.644Z"), rejection("2026-09-15T03:51:22.725Z"))
        self.assertEqual(CodexLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0933))

    def test_message_time_is_the_fallback(self):
        # Snapshot not used up (e.g. it came from an earlier turn): read the message.
        self.write("15", "rollout-a.jsonl", limits("2026-09-15T03:40:00Z", primary=97.0),
                   rejection("2026-09-15T03:51:22Z", "You've hit your usage limit. Upgrade to Pro "
                             "(https://chatgpt.com/explore/pro) or try again at Sep 15th, 2026 9:33 AM."))
        self.assertEqual(CodexLog(self.root).latest_reset(), dt.datetime(2026, 9, 15, 9, 33))

    def test_newest_rejection_across_logs_wins(self):
        self.write("14", "rollout-old.jsonl", limits("2026-09-14T10:00:00Z", resets_at=RESET_0933 - 86400),
                   rejection("2026-09-14T10:00:01Z"))
        self.write("15", "rollout-new.jsonl", limits("2026-09-15T03:51:22Z"), rejection("2026-09-15T03:51:23Z"))
        self.assertEqual(CodexLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0933))

    def test_prose_about_limits_is_ignored(self):
        user = json.dumps({"timestamp": "2026-09-15T04:00:00Z", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text":
                "I hit my usage limit (usage_limit_exceeded, task_complete) - try again at 9:33 AM"}]}})
        other = rejection("2026-09-15T04:00:01Z", info="server_overloaded")
        self.write("15", "rollout-a.jsonl", limits("2026-09-15T03:59:00Z"), user, other)
        self.assertIsNone(CodexLog(self.root).latest_reset())

    def test_try_again_later_without_snapshot_gives_nothing(self):
        self.write("15", "rollout-a.jsonl", rejection("2026-09-15T03:51:22Z", "You've hit your usage limit. "
                                                      "Try again later."))
        self.assertIsNone(CodexLog(self.root).latest_reset())

    def test_record_at_the_end_of_a_large_log_is_found(self):
        filler = json.dumps({"timestamp": "2026-09-15T03:00:00Z", "type": "response_item",
                             "payload": {"type": "reasoning", "text": "x" * 4000}})
        self.write("15", "rollout-big.jsonl", *([filler] * 400), limits("2026-09-15T03:51:22Z"),
                   rejection("2026-09-15T03:51:23Z"))
        self.assertEqual(CodexLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0933))

    def test_unchanged_logs_are_not_read_again(self):
        self.write("15", "rollout-a.jsonl", limits("2026-09-15T03:51:22Z"), rejection("2026-09-15T03:51:23Z"))
        log = CodexLog(self.root)
        self.assertIsNotNone(log.latest_reset())
        with patch.object(log, "_scan", side_effect=AssertionError("re-read an unchanged log")):
            self.assertIsNotNone(log.latest_reset())

    def test_stale_logs_and_missing_folder_give_nothing(self):
        path = self.write("01", "rollout-a.jsonl", limits("2026-09-01T03:51:22Z"), rejection("2026-09-01T03:51:23Z"))
        old = time.time() - codex_log.MAX_AGE_S - 60
        os.utime(path, (old, old))
        self.assertIsNone(CodexLog(self.root).latest_reset())
        self.assertIsNone(CodexLog(os.path.join(self.root, "missing")).latest_reset())

    def test_codex_home_moves_the_folder(self):
        with patch.dict(os.environ, {"CODEX_HOME": r"D:\codex-home"}):
            self.assertEqual(codex_log.default_root(), os.path.join(r"D:\codex-home", "sessions"))


if __name__ == "__main__":
    unittest.main()
