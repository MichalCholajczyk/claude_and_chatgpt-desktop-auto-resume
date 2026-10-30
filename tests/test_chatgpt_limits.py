"""Parsing of ChatGPT's usage-limit texts (notice, profile-menu rows, Codex logs)."""
import datetime as dt
import unittest

from chatgpt_limits import (blocked_until, is_notice, notice_reset, parse_moment,
                            parse_usage_rows, summarize, window_minutes)

NOW = dt.datetime(2026, 9, 21, 21, 40)


class NoticeTests(unittest.TestCase):
    def test_notices_in_both_languages(self):
        for text in ("You've hit your usage limit. Try again later.",
                     "You’ve hit your usage limit. Upgrade your plan or add credits to continue, or try again later.",
                     "Osiągnięto limit użycia. Przejdź na wyższy plan lub doładuj kredyty, aby kontynuować, "
                     "albo spróbuj ponownie później.",
                     "You've hit your usage limit for GPT-6. Try again later, or start a new conversation with another model.",
                     "GPT-6 ma już wyczerpany limit użycia. Spróbuj ponownie później lub rozpocznij nową konwersację."):
            self.assertTrue(is_notice(text), text)

    def test_prose_and_ui_labels_are_not_notices(self):
        for text in ("Usage remaining", "Pozostały limit", "5 godz.", "I hit my usage limit while you were working",
                     "The notice says: You've hit your usage limit.", "Osiągnięto limit załączników plikowych",
                     "limit użycia 5-godzinny", ""):
            self.assertFalse(is_notice(text), text)

    def test_reset_is_read_only_while_the_notice_shows_a_time(self):
        self.assertEqual(notice_reset("You've hit your usage limit. Try again at 11:05 PM.", NOW),
                         dt.datetime(2026, 9, 21, 23, 5))
        self.assertEqual(notice_reset("Osiągnięto limit użycia. Spróbuj ponownie 23:05.", NOW),
                         dt.datetime(2026, 9, 21, 23, 5))
        self.assertIsNone(notice_reset("Osiągnięto limit użycia. Spróbuj ponownie później.", NOW))
        self.assertIsNone(notice_reset("You've hit your usage limit. Try again later.", NOW))

    def test_time_before_now_is_tomorrow(self):
        # "try again at 2:39 AM" read at 21:40 is the coming night, not this morning.
        self.assertEqual(notice_reset("You've hit your usage limit. Try again at 2:39 AM.", NOW),
                         dt.datetime(2026, 9, 22, 2, 39))

    def test_codex_log_messages(self):
        text = ("You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit "
                "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at "
                "Sep 10th, 2026 12:11 AM.")
        self.assertEqual(notice_reset(text, dt.datetime(2026, 9, 9, 19, 31)), dt.datetime(2026, 9, 10, 0, 11))
        text = "You've hit your usage limit. ... or try again at 9:33 AM."
        self.assertEqual(notice_reset(text, dt.datetime(2026, 9, 15, 5, 51)), dt.datetime(2026, 9, 15, 9, 33))


class MomentTests(unittest.TestCase):
    def test_times_dates_and_now(self):
        cases = {"02:39": dt.datetime(2026, 9, 22, 2, 39), "2:39 AM": dt.datetime(2026, 9, 22, 2, 39),
                 "11:15 PM": dt.datetime(2026, 9, 21, 23, 15), "12:05 a.m.": dt.datetime(2026, 9, 22, 0, 5),
                 "28 wrz": dt.datetime(2026, 9, 28), "Sep 28": dt.datetime(2026, 9, 28),
                 "3 paź": dt.datetime(2026, 10, 3), "15 wrz, 09:33": dt.datetime(2026, 9, 15, 9, 33),
                 "15.09.2026, 09:33": dt.datetime(2026, 9, 15, 9, 33), "9/15/2026, 9:33 AM": dt.datetime(2026, 9, 15, 9, 33),
                 "now": NOW, "teraz": NOW}
        for text, expected in cases.items():
            self.assertEqual(parse_moment(text, NOW), expected, text)

    def test_a_time_just_passed_is_not_moved_to_tomorrow(self):
        self.assertEqual(parse_moment("21:39", NOW), dt.datetime(2026, 9, 21, 21, 39))

    def test_january_date_read_in_december_is_next_year(self):
        self.assertEqual(parse_moment("Jan 2", dt.datetime(2026, 12, 30, 10, 0)), dt.datetime(2027, 1, 2))

    def test_labels_are_not_moments(self):
        for text in ("5h", "5 godz.", "Weekly", "Co tydzień", "100%", "GPT-6 limit:", "Usage remaining"):
            self.assertIsNone(parse_moment(text, NOW), text)


class MenuRowTests(unittest.TestCase):
    # Captured from the profile menu on 21.09 (Polish UI), after expanding "Pozostały limit".
    POLISH = ["5 godz.", "100%", "02:39", "Co tydzień", "100%", "28 wrz"]

    def test_polish_rows(self):
        rows = parse_usage_rows(self.POLISH, NOW)
        self.assertEqual([(r["minutes"], r["left"], r["reset"]) for r in rows],
                         [(300, 100, dt.datetime(2026, 9, 22, 2, 39)), (10080, 100, dt.datetime(2026, 9, 28))])
        self.assertEqual(blocked_until(rows), (False, None))

    def test_english_rows_with_a_used_up_window(self):
        rows = parse_usage_rows(["5h", "0%", "11:05 PM", "Weekly", "62%", "Sep 28"], NOW)
        self.assertEqual(blocked_until(rows), (True, dt.datetime(2026, 9, 21, 23, 5)))
        self.assertEqual(summarize(rows), {"5h": dict(pct=100, left=0, reset=dt.datetime(2026, 9, 21, 23, 5)),
                                           "weekly": dict(pct=38, left=62, reset=dt.datetime(2026, 9, 28))})

    def test_row_without_reset_and_model_section(self):
        rows = parse_usage_rows(["5h", "40%", "Weekly", "0%", "Sep 28", "GPT-6 limit:", "5h", "0%", "10:00 PM"], NOW)
        self.assertEqual([(r["label"], r["left"], r["model"]) for r in rows],
                         [("5h", 40, None), ("Weekly", 0, None), ("5h", 0, "GPT-6")])
        self.assertIsNone(rows[0]["reset"])
        # Both used-up windows must clear: the weekly one ends later.
        self.assertEqual(blocked_until(rows), (True, dt.datetime(2026, 9, 28)))
        self.assertEqual(set(summarize(rows)), {"5h", "weekly"})

    def test_used_up_window_without_reset_is_blocked_with_unknown_time(self):
        rows = parse_usage_rows(["5h", "0%"], NOW)
        self.assertEqual(blocked_until(rows), (True, None))

    def test_window_labels(self):
        cases = {"5h": 300, "5 godz.": 300, "1d": 1440, "30m": 30, "Weekly": 10080, "Co tydzień": 10080,
                 "2 Weeks": 20160, "Co 2 tygodnie": 20160, "Monthly": 43200, "?": None, "": None}
        for label, minutes in cases.items():
            self.assertEqual(window_minutes(label), minutes, label)


if __name__ == "__main__":
    unittest.main()
