"""Tests for reading the latest usage-limit reset from Claude Code's local logs."""
import datetime as dt
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import quota_log
from quota_log import QuotaLog

RESET_0930 = 1789457400   # 2026-09-15 07:30 UTC, as written by Claude Code on 15.09


def rejection(timestamp, resets_at, status="rejected", error="rate_limit"):
    return json.dumps({"type": "assistant", "timestamp": timestamp, "error": error,
                       "message": {"content": [{"type": "text", "text": "You've hit your session limit"}]},
                       "quotaLimits": {"status": status, "resetsAt": resets_at,
                                       "rateLimitType": "five_hour"}})


class QuotaLogTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, project, name, *lines):
        folder = os.path.join(self.root, project)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_newest_rejection_across_logs_wins(self):
        self.write("flow-chart", "older.jsonl",
                   rejection("2026-09-15T07:25:41.669Z", RESET_0930),
                   json.dumps({"type": "user", "timestamp": "2026-09-15T07:26:00Z"}))
        self.write("fitness", "newer.jsonl", rejection("2026-09-15T12:40:00.000Z", RESET_0930 + 5 * 3600))
        self.write("fitness", "earlier.jsonl", rejection("2026-09-15T05:05:19.913Z", RESET_0930))
        self.assertEqual(QuotaLog(self.root).latest_reset(),
                         dt.datetime.fromtimestamp(RESET_0930 + 5 * 3600))

    def test_only_rejected_rate_limit_records_count(self):
        self.write("p", "s.jsonl",
                   rejection("2026-09-15T05:05:19Z", RESET_0930),
                   "not json at all",
                   rejection("2026-09-15T06:00:00Z", RESET_0930 + 3600, status="allowed_warning"),
                   rejection("2026-09-15T06:30:00Z", RESET_0930 + 7200, error="overloaded"),
                   '{"type": "assistant", "quotaLimits": {"status": "rejected", "resetsAt": ')
        self.assertEqual(QuotaLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0930))

    def test_record_at_the_end_of_a_large_log_is_found(self):
        padding = json.dumps({"type": "user", "timestamp": "2026-09-15T05:00:00Z", "blob": "x" * 4096})
        self.write("p", "s.jsonl", *([padding] * 40), rejection("2026-09-15T07:25:41Z", RESET_0930))
        with patch.object(quota_log, "TAIL_BYTES", 8192):
            self.assertEqual(QuotaLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0930))

    def test_unchanged_logs_are_not_read_again(self):
        path = self.write("p", "s.jsonl", rejection("2026-09-15T07:25:41Z", RESET_0930))
        log = QuotaLog(self.root)
        with patch.object(QuotaLog, "_scan", autospec=True, side_effect=QuotaLog._scan) as scan:
            log.latest_reset()
            log.latest_reset()
            self.assertEqual(scan.call_count, 1)
            with open(path, "a", encoding="utf-8") as f:
                f.write(rejection("2026-09-15T12:40:00Z", RESET_0930 + 5 * 3600) + "\n")
            self.assertEqual(log.latest_reset(), dt.datetime.fromtimestamp(RESET_0930 + 5 * 3600))
            self.assertEqual(scan.call_count, 2)

    def test_stale_logs_and_missing_folder_give_nothing(self):
        path = self.write("p", "s.jsonl", rejection("2026-08-01T07:25:41Z", RESET_0930))
        old = time.time() - quota_log.MAX_AGE_S - 3600
        os.utime(path, (old, old))
        self.assertIsNone(QuotaLog(self.root).latest_reset())
        self.assertIsNone(QuotaLog(os.path.join(self.root, "missing")).latest_reset())


def titled(title):
    return json.dumps({"type": "custom-title", "customTitle": title, "sessionId": "s1"})


def wrap_note(timestamp="2026-10-15T07:05:17.123Z", cwd="C:\\Users\\micha\\Desktop\\projekt", branch="main"):
    """Claude Code's hidden note at the moment the limit ran out mid-response (seen 15.10)."""
    return json.dumps({"type": "user", "isMeta": True, "usageLimitNote": "wrap_up", "cwd": cwd,
                       "gitBranch": branch, "timestamp": timestamp,
                       "message": {"role": "user", "content": "Usage limit reached; a short grace allowance remains."}})


def reply(text=None, stop="end_turn", msg_id="msg_9", sidechain=False, tool=False):
    """One record of an assistant message; Claude Code logs each content block on its own."""
    content = ([{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}] if tool
               else [{"type": "text", "text": text}])
    return json.dumps({"type": "assistant", "isSidechain": sidechain, "timestamp": "2026-10-15T07:05:51Z",
                       "message": {"id": msg_id, "role": "assistant", "content": content,
                                   "stop_reason": stop, "usage": {"input_tokens": 10}}})


def said(text, blocks=False):
    """A message the person typed: its content is a string or a list of text blocks (both
    are in real logs, 15.10)."""
    content = [{"type": "text", "text": text}] if blocks else text
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}})


SUMMARY = ("Zatrzymałem się w bezpiecznym miejscu.\n\n**Zrobione:** 7 z 9.\n"
           "**Zostało:** changelog i `C:\\docs\\plan.md`.")


def utc_local(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


class WrapUpLogTests(unittest.TestCase):
    """What Claude Code's log says about a wrap-up (2.1.277+): the note, then the summary."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, name, *lines, project="C--Users-micha-Desktop-projekt"):
        """A log in the folder Claude Code names after where the conversation started."""
        folder = os.path.join(self.root, project)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def wrap_up(self, title="Plan grup"):
        return QuotaLog(self.root).wrap_up_for(title)

    def test_the_folder_is_the_project_even_when_the_shell_moved_deeper(self):
        # A real note had a cwd five levels below the project (review, 15.10).
        self.write("s.jsonl", titled("Plan grup"),
                   wrap_note(cwd="C:\\Users\\micha\\Desktop\\projekt\\apps\\web\\src"), reply(SUMMARY))
        self.assertEqual(self.wrap_up()["folder"], "C:\\Users\\micha\\Desktop\\projekt")

    def test_a_folder_that_does_not_match_the_log_is_not_given(self):
        self.write("s.jsonl", titled("Plan grup"), wrap_note(cwd="D:\\inny\\folder"), reply(SUMMARY))
        self.assertEqual(self.wrap_up()["folder"], "")

    def test_the_wrap_up_gives_its_summary_folder_branch_and_time(self):
        self.write("s.jsonl", titled("Plan grup"), reply("Pracuję.", stop="tool_use", msg_id="msg_1"),
                   wrap_note(), reply(stop="tool_use", tool=True, msg_id="msg_8"),
                   json.dumps({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
                   reply(SUMMARY))
        self.assertEqual(self.wrap_up(), dict(summary=SUMMARY, folder="C:\\Users\\micha\\Desktop\\projekt",
                                              branch="main", at=utc_local("2026-10-15T07:05:17.123Z")))

    def test_a_summary_in_several_records_is_joined(self):
        self.write("s.jsonl", titled("Plan grup"), wrap_note(),
                   reply("Część pierwsza.", stop=None), reply("Część druga."))
        self.assertEqual(self.wrap_up()["summary"], "Część pierwsza.\n\nCzęść druga.")

    def test_a_subagents_reply_is_not_the_summary(self):
        self.write("s.jsonl", titled("Plan grup"), wrap_note(),
                   reply("Raport subagenta.", sidechain=True, msg_id="msg_sub"), reply(SUMMARY))
        self.assertEqual(self.wrap_up()["summary"], SUMMARY)

    def test_the_reply_to_a_later_message_is_not_the_summary(self):
        self.write("s.jsonl", titled("Plan grup"), wrap_note(), reply(SUMMARY),
                   json.dumps({"type": "user", "message": {"content": "dalej"}}),
                   reply("Kolejna odpowiedź.", msg_id="msg_10"))
        self.assertEqual(self.wrap_up()["summary"], SUMMARY)

    def test_a_turn_cut_short_does_not_take_the_reply_to_a_later_message(self):
        # The wrap-up's turn ended in a rejected request, not in a summary, and then the
        # person wrote again (review, 15.10).
        for blocks in (False, True):
            with self.subTest(blocks=blocks):
                self.write("s.jsonl", titled("Plan grup"), wrap_note(),
                           reply("Sprawdzam jeszcze testy.", stop="tool_use", msg_id="msg_8"),
                           rejection("2026-10-15T07:06:00Z", RESET_0930),
                           said("dalej", blocks), reply("Kolejna odpowiedź.", msg_id="msg_10"))
                self.assertIsNone(self.wrap_up())

    def test_hidden_notes_and_a_subagents_prompt_do_not_end_the_turn(self):
        # Claude Code also writes texts of its own as user records ("isMeta"), mid-turn too.
        self.write("s.jsonl", titled("Plan grup"), wrap_note(),
                   json.dumps({"type": "user", "isMeta": True,
                               "message": {"role": "user", "content": [{"type": "text", "text": "Notatka."}]}}),
                   json.dumps({"type": "user", "isSidechain": True,
                               "message": {"role": "user", "content": "Zbadaj moduł."}}),
                   reply(SUMMARY))
        self.assertEqual(self.wrap_up()["summary"], SUMMARY)

    def test_without_a_closing_reply_there_is_no_wrap_up(self):
        self.write("s.jsonl", titled("Plan grup"), wrap_note(), reply(stop="tool_use", tool=True))
        self.assertIsNone(self.wrap_up())

    def test_a_log_without_a_wrap_up_gives_nothing(self):
        self.write("s.jsonl", titled("Plan grup"), reply(SUMMARY))
        self.assertIsNone(self.wrap_up())

    def test_only_the_conversations_own_log_counts(self):
        self.write("s.jsonl", titled("Inna rozmowa"), wrap_note(), reply(SUMMARY))
        self.assertIsNone(self.wrap_up())

    def test_a_renamed_conversation_counts_under_its_newest_title(self):
        self.write("s.jsonl", titled("Stary tytuł"), wrap_note(), reply(SUMMARY), titled("Plan grup"))
        self.assertEqual(self.wrap_up()["summary"], SUMMARY)
        self.assertIsNone(self.wrap_up("Stary tytuł"))

    def test_the_newest_log_with_the_title_counts(self):
        older = self.write("old.jsonl", titled("Plan grup"), wrap_note(), reply(SUMMARY))
        self.write("new.jsonl", titled("Plan grup"), reply("Nowa sesja o tym samym tytule."))
        stamp = time.time() - 3600
        os.utime(older, (stamp, stamp))
        self.assertIsNone(self.wrap_up())

    def test_a_release_note_after_the_wrap_up_does_not_hide_it(self):
        # Claude Code also logs {"usageLimitNote": "release"} (seen 15.10 in older logs).
        self.write("s.jsonl", titled("Plan grup"), wrap_note(), reply(SUMMARY),
                   json.dumps({"type": "user", "isMeta": True, "usageLimitNote": "release",
                               "timestamp": "2026-10-15T12:00:00Z"}))
        self.assertEqual(self.wrap_up()["summary"], SUMMARY)

    def test_a_tail_read_for_the_context_is_not_read_again_for_the_title(self):
        self.write("s.jsonl", titled("Plan grup"), wrap_note(), reply(SUMMARY))
        log = QuotaLog(self.root)
        self.assertEqual(log.context_for("Plan grup")["used"], 10)
        with patch.object(quota_log, "tail_lines", wraps=quota_log.tail_lines) as read:
            self.assertEqual(log.wrap_up_for("Plan grup")["summary"], SUMMARY)
        # Only the wrap-up's own, longer tail is read: the title is known already (review, 15.10).
        self.assertEqual([call.args[2] for call in read.call_args_list], [quota_log.WRAPUP_TAIL_BYTES])

    def test_a_log_that_grew_old_is_forgotten(self):
        # The program runs for weeks: what it read from a log must not outlive the log's
        # place among the recent ones (review, 15.10).
        path = self.write("stara-rozmowa.jsonl", titled("Plan grup"), wrap_note(), reply(SUMMARY))
        log = QuotaLog(self.root)
        self.assertEqual(log.wrap_up_for("Plan grup")["summary"], SUMMARY)
        old = time.time() - quota_log.MAX_AGE_S - 3600
        os.utime(path, (old, old))
        self.assertIsNone(log.wrap_up_for("Plan grup"))
        self.assertNotIn("stara-rozmowa.jsonl", repr(vars(log)))

    def test_broken_lines_are_skipped(self):
        self.write("s.jsonl", titled("Plan grup"), "not json", wrap_note(),
                   '{"type": "assistant", "message": ', reply(SUMMARY), '{"type": "custom-tit')
        self.assertEqual(self.wrap_up()["summary"], SUMMARY)


if __name__ == "__main__":
    unittest.main()
