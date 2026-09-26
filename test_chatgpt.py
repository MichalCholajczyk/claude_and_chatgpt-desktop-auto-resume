"""ChatGPT Auto-Resume: discovery, signals, the physical usage check and scheduling
(no real input; the fixtures mirror the ChatGPT app's accessibility tree of 21.09)."""
import datetime as dt
import queue
import types
import unittest
from unittest.mock import Mock, patch

import chatgpt_auto_continue as gpt
from chatgpt_automation import (ChatGPTEngine, ChatGPTUI, discover, signals, transcript)
from session_automation import Node, WindowHidden, content_hidden
from test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


NOW = dt.datetime(2026, 9, 21, 21, 40)
NOTICE_PL = ("Osiągnięto limit użycia. Przejdź na wyższy plan lub doładuj kredyty, aby kontynuować, "
             "albo spróbuj ponownie później.")
NOTICE_EN_AT = "You've hit your usage limit. Upgrade your plan or add credits to continue, or try again at 11:05 PM."
# 23.09: the notice with its time, and the account-wide card ChatGPT put above the composer.
NOW_2309 = dt.datetime(2026, 9, 23, 10, 10)
NOTICE_PL_AT = ("Osiągnięto limit użycia. Zmień plan lub doładuj kredyty, aby kontynuować, "
                "albo spróbuj ponownie 14:05.")
BANNER_PL = ("Osiągnięto limit korzystania z Codex i Work",
             "Twój limit zapytań odnowi się 14:05. Przejdź na wyższy plan lub wykorzystaj teraz jedno "
             "z odnowień limitu zapytań.")


def node(name="", kind="GroupControl", parent=None, rect=(10, 10, 300, 300), role="", control=None):
    n = Node(name, kind, rect, control, parent=parent, role=role)
    if parent:
        parent.children.append(n)
    return n


def composer(value=""):
    return types.SimpleNamespace(GetValuePattern=lambda: types.SimpleNamespace(Value=value))


class EditableComposer:
    """A composer whose value can be set through its ValuePattern."""

    def __init__(self, value=""):
        self.Value, self.IsReadOnly, self.HasKeyboardFocus = value, False, True

    def GetValuePattern(self):
        return self

    def SetValue(self, value):
        self.Value = value


class Window:
    """A ChatGPT window: sidebar (mode switch, projects, chats, profile button),
    main area (header, transcript, composer)."""

    def __init__(self, title="Fix the parser", mode="Codex", chats=("Fix the parser", "Write the docs"),
                 lang="pl", submit=None, draft=""):
        pl = lang == "pl"
        self.root = node("window", "WindowControl")
        self.doc = node(title, "DocumentControl", self.root, (0, 0, 1280, 1392), role="document")
        shell = node(parent=self.doc)
        side = node(parent=shell, rect=(0, 36, 323, 1392), role="complementary")
        node(("Zmień tryb, bieżący tryb: " if pl else "Switch mode, current mode: ") + mode,
             "ButtonControl", side, (8, 44, 95, 76))
        node("Nowy czat" if pl else "New chat", "ButtonControl", side, (8, 84, 283, 114))
        project = node("demo-project", parent=side)
        folder = node("demo-project", "ButtonControl", project, (8, 283, 305, 313))
        node(("Działania projektu dot.: " if pl else "Project actions for: ") + "demo-project",
             "ButtonControl", folder, (305, 286, 329, 310))
        self.rows = {}
        for i, chat in enumerate(chats):
            item = node(chat, "ListItemControl", project, (8, 314 + 31 * i, 305, 345 + 31 * i))
            row = node(chat, "ButtonControl", node(parent=item), (8, 314 + 31 * i, 305, 344 + 31 * i))
            actions = node(parent=row)
            node("Przypnij czat" if pl else "Pin chat", "ButtonControl", node(parent=actions), (251, 319, 270, 339))
            node("Archiwizuj czat" if pl else "Archive chat", "ButtonControl", node(parent=actions), (278, 319, 297, 339))
            node(chat, "TextControl", node(parent=row), (40, 319, 213, 338))
            self.rows[chat] = row
        footer = node(parent=side, rect=(0, 1346, 323, 1392))
        node("Otwórz menu profilu" if pl else "Open profile menu", "ButtonControl", footer, (8, 1354, 275, 1384))
        self.main = node(parent=shell, rect=(323, 36, 1280, 1392), role="main")
        header = node(parent=self.main, rect=(323, 36, 1280, 82))
        node(("Projekt: " if pl else "Project: ") + "demo-project", "ButtonControl", header, (337, 45, 365, 73))
        self.title_button = node(title, "ButtonControl", node(parent=header), (365, 47, 555, 71))
        node("Udostępnij" if pl else "Share", "ButtonControl", header, (1103, 45, 1204, 73))
        thread = node(parent=self.main, rect=(324, 83, 1280, 1392))
        self.feed = node(parent=thread, rect=(434, 115, 1170, 1001))
        # The composer has wrappers of its own, shared with a floating element (captured 23.09).
        self.dock = node(parent=thread, rect=(324, 1278, 1280, 1392))
        node(parent=self.dock, rect=(783, 1222, 816, 1254))
        box = node(parent=self.dock, rect=(434, 1278, 1170, 1376))
        self.thread, self.box = thread, box
        self.prompt = node("Zleć cokolwiek" if pl else "Do anything", "EditControl", box,
                           (446, 1292, 1158, 1336), role="textbox", control=composer(draft))
        if draft:
            node(draft, "TextControl", node(parent=self.prompt), (446, 1292, 974, 1311))
        node("Dodaj pliki i nie tylko" if pl else "Add files and more", "ButtonControl", node(parent=box),
             (442, 1340, 470, 1368))
        # With text the submit button is "Send"; an empty composer shows "Start voice chat".
        idle = ("Wyślij" if pl else "Send") if draft else ("Rozpocznij czat głosowy" if pl else "Start voice chat")
        self.submit = node(submit or idle, "ButtonControl", box, (1134, 1340, 1162, 1368))
        node("Środowisko" if pl else "Environment", "ButtonControl", thread, (1337, 104, 1413, 123))
        self.pl = pl

    def said(self, who, *texts):
        """A transcript message: a role heading, then text groups."""
        label = {("user", True): "Twoja wiadomość:", ("user", False): "You said:",
                 ("bot", True): "ChatGPT powiedział:", ("bot", False): "ChatGPT said:"}[who, self.pl]
        node(label, "TextControl", node(label, "TextControl", self.feed, role="heading"))
        for text in texts:
            node(text, "TextControl", node(parent=self.feed))
        return self

    def notice(self, text=NOTICE_PL, parent=None):
        node(text, "TextControl", node(parent=parent or self.feed, rect=(434, 946, 1170, 989)), (475, 947, 1151, 988))
        return self

    def newer_turn(self, activity=("Rozszerzony zestaw przeszedł 22 testy.",),
                   final="Kod diagnostyki jest już zapisany w osobnym commicie.",
                   divider="Przetwarzano przez 32 min 37 s"):
        """The newer turn layout (localConversation.workedFor.v2, on the other PC on 23.09):
        the agent's activity, a duration divider, then the final response, each in a
        container of its own — the notice is not a sibling of the last role heading."""
        self.turn = node(parent=self.feed, rect=(434, 300, 1170, 940))
        activity_box = node(parent=self.turn, rect=(434, 300, 1170, 700))
        for i, text in enumerate(activity):
            heading = node("ChatGPT powiedział:", "TextControl", activity_box, role="heading")
            node("ChatGPT powiedział:", "TextControl", heading)
            node(text, "TextControl", node(parent=activity_box, rect=(434, 310, 1170, 350)), (434, 310, 1170, 350))
            summary = node(parent=activity_box, rect=(434, 360, 800, 382))
            node("Edytowano pliki i uruchomił polecenia", "ButtonControl", summary, (434, 360, 800, 382))
        node(divider, "TextControl", node(parent=self.turn, rect=(434, 710, 1170, 740)), (434, 712, 640, 732))
        final_box = node(parent=self.turn, rect=(434, 750, 1170, 900))
        node(final, "TextControl", node(parent=final_box, rect=(434, 750, 1170, 850)), (434, 750, 1170, 850))
        summary = node(parent=final_box, rect=(434, 870, 800, 892))
        node("Edytowano pliki i uruchomił polecenia", "ButtonControl", summary, (434, 870, 800, 892))
        return self

    def changed_files(self):
        """What follows a turn that edited files (captured 23.09): the changed-files card,
        a button per file, "Show 1 more file", the message actions and the time stamp."""
        card = node(parent=self.feed, rect=(434, 1000, 1170, 1065))
        node("Przejrzyj zmienione pliki", "ButtonControl", card, (434, 1000, 1170, 1065))
        node("Edytowano 4 pliki", "TextControl", card, (496, 1013, 555, 1033))
        counts = node(parent=card, rect=(496, 1036, 541, 1050))
        node("+80", "TextControl", counts, (496, 1034, 518, 1052))
        node("-20", "TextControl", counts, (521, 1034, 541, 1052))
        undo = node("Cofnij", "ButtonControl", card, (570, 1018, 640, 1047))
        node("Cofnij", "TextControl", undo, (579, 1023, 613, 1041))
        node("Zrecenzuj", "ButtonControl", card, (647, 1018, 721, 1047))
        item = node("session_automation.py +18 -8", "ButtonControl", self.feed, (434, 1065, 1170, 1102))
        node("session_automation.py", "TextControl", item, (445, 1084, 585, 1104))
        node("+18", "TextControl", item, (1115, 1073, 1140, 1093))
        node("-8", "TextControl", item, (1143, 1073, 1156, 1093))
        more = node("Pokaż jeszcze 1 plik", "ButtonControl", self.feed, (434, 1101, 1170, 1138))
        node("Pokaż jeszcze 1 plik", "TextControl", more, (446, 1110, 567, 1130))
        for name, x in (("Kopiuj", 430), ("Oceń odpowiedź", 458)):
            node(name, "ButtonControl", node(parent=self.feed, rect=(x, 1148, x + 26, 1175)), (x, 1148, x + 26, 1175))
        node("10 wrz, 4:43", "TextControl", self.feed, (520, 1153, 583, 1170))
        return self

    def banner(self, texts=BANNER_PL, actions=("Przejdź na wyższy plan", "Resetuj użycie"), outside=False):
        """The account-wide limit card above the composer: in the composer's wrapper
        (like the floating element there), or `outside` it, between it and the transcript."""
        parent, before = (self.thread, self.dock) if outside else (self.dock, self.box)
        card = Node("", "GroupControl", (434, 1180, 1170, 1270), None, parent=parent)
        parent.children.insert(parent.children.index(before), card)
        for i, text in enumerate(texts):
            node(text, "TextControl", card, (470, 1190 + 25 * i, 1150, 1210 + 25 * i))
        for i, name in enumerate(actions):
            node(name, "ButtonControl", card, (470 + 160 * i, 1240, 620 + 160 * i, 1265))
        return self


class DiscoveryTests(unittest.TestCase):
    def test_open_conversation_and_sidebar_chats(self):
        w = Window()
        panes, entries = discover(w.root)
        self.assertEqual([(p.key, p.title) for p in panes], [("codex:Fix the parser", "Fix the parser")])
        self.assertIs(panes[0].prompt, w.prompt)
        self.assertEqual([e["key"] for e in entries], ["codex:Fix the parser", "codex:Write the docs"])
        self.assertIs(entries[1]["node"], w.rows["Write the docs"])

    def test_project_folders_and_row_actions_are_not_chats(self):
        titles = [e["title"] for e in discover(Window(lang="en").root)[1]]
        self.assertNotIn("demo-project", titles)
        self.assertNotIn("Pin chat", titles)
        self.assertNotIn("Archive chat", titles)

    def test_mode_is_part_of_the_key(self):
        panes, entries = discover(Window(mode="Chat", lang="en").root)
        self.assertEqual(panes[0].key, "chat:Fix the parser")
        self.assertTrue(all(e["key"].startswith("chat:") for e in entries))

    def test_home_page_is_not_a_conversation(self):
        w = Window(title="ChatGPT")
        w.title_button.name = "Nowy czat"
        self.assertEqual(discover(w.root)[0], [])

    def test_title_elsewhere_in_the_page_does_not_make_a_conversation(self):
        w = Window(title="ChatGPT")
        w.title_button.name = "Nowy czat"
        node("ChatGPT", "ButtonControl", w.feed, (500, 600, 700, 630))     # e.g. a link in a message
        self.assertEqual(discover(w.root)[0], [])

    def test_second_composer_is_ambiguous(self):
        w = Window()
        side_chat = node(parent=w.main, rect=(900, 900, 1270, 1300))
        node("Zapytaj", "EditControl", side_chat, (905, 1200, 1260, 1240), role="textbox", control=composer())
        node("Dodaj pliki i nie tylko", "ButtonControl", side_chat, (905, 1250, 930, 1270))
        node("Wyślij", "ButtonControl", side_chat, (1230, 1250, 1260, 1270))
        self.assertEqual(discover(w.root)[0], [])

    def test_hidden_window_is_detected(self):
        w = Window()
        self.assertFalse(content_hidden(w.root))
        hidden = node("window", "WindowControl")
        node("", "DocumentControl", hidden, (0, 0, 0, 0), role="document")
        self.assertTrue(content_hidden(hidden))


class SignalTests(unittest.TestCase):
    def test_notice_ending_the_conversation_is_a_limit(self):
        w = Window().said("user", "Zbuduj aplikację").said("bot", "Sprawdzam kod.").notice()
        s = signals(discover(w.root)[0][0], NOW)
        self.assertEqual((s["limit"], s["reset"], s["busy"], s["error"]), (True, None, False, None))

    def test_notice_with_time_gives_the_reset(self):
        w = Window(lang="en").said("user", "Build it").said("bot", "Working on it.").notice(NOTICE_EN_AT)
        s = signals(discover(w.root)[0][0], NOW)
        self.assertEqual((s["limit"], s["reset"]), (True, dt.datetime(2026, 9, 21, 23, 5)))

    def test_time_stamp_or_duration_after_the_notice_still_counts(self):
        w = Window().said("user", "Zbuduj aplikację").said("bot", "Sprawdzam.").notice()
        node("wtorek, 05:51", "TextControl", w.feed)
        node("Pracował przez 34 min", "TextControl", w.feed)
        self.assertTrue(signals(discover(w.root)[0][0], NOW)["limit"])

    def test_limit_card_above_the_composer_does_not_hide_the_notice(self):
        # 23.09 regression: the card's texts came last, so the notice was never seen.
        w = Window().said("user", "Uruchom projekt").said("bot", "Zapisuję raport.").notice(NOTICE_PL_AT).banner()
        s = signals(discover(w.root)[0][0], NOW_2309)
        self.assertEqual((s["limit"], s["reset"], s["busy"], s["retry"]),
                         (True, dt.datetime(2026, 9, 23, 14, 5), False, None))   # "Resetuj użycie" is no retry

    def test_changed_files_card_after_the_notice_is_not_conversation(self):
        w = Window().said("user", "Zbuduj aplikację").said("bot", "Gotowe.").notice(NOTICE_PL_AT).changed_files()
        s = signals(discover(w.root)[0][0], NOW_2309)
        self.assertEqual((s["limit"], s["reset"]), (True, dt.datetime(2026, 9, 23, 14, 5)))
        w.banner()
        self.assertTrue(signals(discover(w.root)[0][0], NOW_2309)["limit"])

    def test_notice_with_its_time_in_a_separate_element(self):
        w = Window().said("user", "x").said("bot", "y")
        row = node(parent=w.feed, rect=(434, 946, 1170, 989))
        for text in ("Osiągnięto limit użycia. Spróbuj ponownie ", "14:05", "."):
            node(text, "TextControl", row, (475, 947, 1151, 988))
        s = signals(discover(w.root)[0][0], NOW_2309)
        self.assertEqual((s["limit"], s["reset"]), (True, dt.datetime(2026, 9, 23, 14, 5)))

    def test_message_text_after_the_notice_is_not_a_limit(self):
        w = Window().said("user", "x").said("bot", "y").notice().said("bot", "Kontynuuję pracę.")
        self.assertFalse(signals(discover(w.root)[0][0], NOW)["limit"])
        w = Window().said("user", "x").said("bot", "y").notice()
        node("Pracuję…", "TextControl", node(parent=w.feed))
        s = signals(discover(w.root)[0][0], NOW)
        self.assertEqual((s["limit"], s["busy"]), (False, True))

    def test_used_up_account_accepts_a_notice_that_is_not_last(self):
        """When "Usage remaining" shows a used-up window, a notice in the last turn is
        enough even if something unknown follows it."""
        w = Window().said("user", "x").said("bot", "y").notice(NOTICE_PL_AT)
        node("Nieznany element", "TextControl", node(parent=w.feed))
        pane = discover(w.root)[0][0]
        self.assertFalse(signals(pane, NOW_2309)["limit"])
        s = signals(pane, NOW_2309, blocked=True)
        self.assertEqual((s["limit"], s["reset"]), (True, dt.datetime(2026, 9, 23, 14, 5)))
        w.said("user", "continue")             # a newer turn: the notice is history either way
        self.assertFalse(signals(discover(w.root)[0][0], NOW_2309, blocked=True)["limit"])

    def test_notice_after_the_newer_turn_layout(self):
        """23.09, second PC: activity, "Przetwarzano przez …", the final response — the
        notice is not a sibling of the last role heading, yet it ends the conversation."""
        reset = dt.datetime(2026, 9, 23, 19, 50)
        notice = NOTICE_PL_AT.replace("14:05", "19:50")
        for inside_turn in (False, True):
            w = Window().said("user", "Uruchom projekt RECLAIM").newer_turn()
            w.notice(notice, parent=w.turn if inside_turn else None).banner()
            s = signals(discover(w.root)[0][0], NOW_2309)
            self.assertEqual((s["limit"], s["reset"], s["busy"]), (True, reset, False), inside_turn)

    def test_limit_card_outside_the_composer_area_is_settled_by_the_menu(self):
        """If the card sits between the transcript and the composer's wrapper, its text
        comes last: the notice is only a candidate until the menu shows a used-up window."""
        w = Window().said("user", "x").said("bot", "y").notice(NOTICE_PL_AT).banner(outside=True)
        pane = discover(w.root)[0][0]
        s = signals(pane, NOW_2309)
        self.assertEqual((s["limit"], s["candidate"]), (False, True))
        s = signals(pane, NOW_2309, blocked=True)
        self.assertEqual((s["limit"], s["candidate"], s["reset"]), (True, False, dt.datetime(2026, 9, 23, 14, 5)))

    def test_icon_glyph_before_the_notice(self):
        w = Window().said("user", "x").said("bot", "y").notice("ⓘ " + NOTICE_PL_AT)
        self.assertTrue(signals(discover(w.root)[0][0], NOW_2309)["limit"])

    def test_newer_dividers_are_not_messages(self):
        w = Window().said("user", "x").said("bot", "y").notice(NOTICE_PL_AT)
        node("Przerwano po 5 min", "TextControl", node(parent=w.feed))
        self.assertTrue(signals(discover(w.root)[0][0], NOW_2309)["limit"])
        w = Window().said("user", "x").said("bot", "Sprawdzam.")
        node("Pracuje od 2 min", "TextControl", node(parent=w.feed))
        s = signals(discover(w.root)[0][0], NOW_2309)
        self.assertEqual((s["limit"], s["busy"]), (False, True))

    def test_error_followed_by_the_changed_files_card_is_still_an_error(self):
        w = Window(lang="en").said("user", "x").said("bot", "stream disconnected before completion").changed_files()
        self.assertEqual(signals(discover(w.root)[0][0], NOW)["error"], "stream disconnected before completion")

    def test_notice_followed_by_a_new_message_is_history(self):
        w = Window().said("user", "Zbuduj aplikację").said("bot", "Sprawdzam.").notice()
        w.said("user", "continue").said("bot", "Kontynuuję pracę.")
        self.assertFalse(signals(discover(w.root)[0][0], NOW)["limit"])

    def test_notice_quoted_in_a_message_is_not_a_limit(self):
        w = Window().said("user", "Co znaczy ten komunikat?").said("bot", "Oznacza koniec limitu:")
        code = node("", "GroupControl", w.feed, role="code")
        node(NOTICE_PL, "TextControl", code)
        self.assertFalse(signals(discover(w.root)[0][0], NOW)["limit"])
        w2 = Window().said("bot", "Komunikat „Osiągnięto limit użycia” pojawia się, gdy skończy się limit.")
        self.assertFalse(signals(discover(w2.root)[0][0], NOW)["limit"])

    def test_header_and_composer_are_not_transcript(self):
        w = Window(draft=NOTICE_PL)
        nodes = transcript(discover(w.root)[0][0])
        self.assertNotIn(w.title_button, nodes)
        self.assertFalse(any(n.name == NOTICE_PL for n in nodes))
        self.assertFalse(signals(discover(w.root)[0][0], NOW)["limit"])

    def test_busy_while_working_or_asking(self):
        for submit in ("Zatrzymaj", "Kolejkuj", "Wykonaj", "Zakończ czat głosowy"):
            w = Window(submit=submit).said("user", "x").said("bot", "Pracuję nad tym.")
            self.assertTrue(signals(discover(w.root)[0][0], NOW)["busy"], submit)
        w = Window().said("bot", "Pytanie?")
        node("Pomiń, pozostało 15 sekund", "ButtonControl", w.feed, (500, 600, 600, 630))
        self.assertTrue(signals(discover(w.root)[0][0], NOW)["busy"])
        w = Window().said("user", "x")
        self.assertFalse(signals(discover(w.root)[0][0], NOW)["busy"])

    def test_server_error_retry_button_and_countdown(self):
        w = Window(lang="en").said("user", "x").said("bot", "Our servers are currently overloaded.")
        button = node("Retry", "ButtonControl", w.feed, (500, 900, 560, 930))
        s = signals(discover(w.root)[0][0], NOW)
        self.assertEqual((s["error"], s["retry"], s["busy"], s["permanent"]),
                         ("Our servers are currently overloaded.", button, False, False))
        button.name = "Retry in 12s"      # ChatGPT is about to retry by itself
        s = signals(discover(w.root)[0][0], NOW)
        self.assertEqual((s["retry"], s["busy"]), (None, True))

    def test_old_error_before_the_latest_message_is_ignored(self):
        w = Window(lang="en").said("bot", "An error occurred during this chat").said("user", "continue")
        self.assertIsNone(signals(discover(w.root)[0][0], NOW)["error"])

    def test_account_error_is_permanent(self):
        w = Window(lang="en").said("bot", "unexpected status 401 Unauthorized: token expired")
        s = signals(discover(w.root)[0][0], NOW)
        self.assertTrue(s["error"] and s["permanent"])


class ComposerTests(unittest.TestCase):
    def test_empty_composer_reporting_its_placeholder_is_empty(self):
        # Captured 21.09: Value "\nZleć cokolwiek" = empty paragraph + placeholder group.
        prompt = node("Zleć cokolwiek", "EditControl", control=composer("\nZleć cokolwiek"))
        node("\n", "TextControl", node(parent=prompt), (0, 0, 0, 0))
        node("Zleć cokolwiek", "TextControl", node(parent=prompt))
        self.assertEqual(ChatGPTUI.value(prompt), "")
        bare = node("Zleć cokolwiek", "EditControl", control=composer("Zleć cokolwiek"))
        self.assertEqual(ChatGPTUI.value(bare), "")

    def test_draft_is_a_draft(self):
        prompt = node("Zleć cokolwiek", "EditControl", control=composer("kontynuuj, dla informacji"))
        node("kontynuuj, dla informacji", "TextControl", node(parent=prompt))
        self.assertEqual(ChatGPTUI.value(prompt), "kontynuuj, dla informacji")

    def test_draft_equal_to_the_placeholder_is_still_a_draft(self):
        prompt = node("Zleć cokolwiek", "EditControl", control=composer("Zleć cokolwiek"))
        node("Zleć cokolwiek", "TextControl", node(parent=prompt))
        self.assertEqual(ChatGPTUI.value(prompt), "Zleć cokolwiek")


class ResumeTests(unittest.TestCase):
    """Typing the message and making sure it was really sent (no real input)."""

    KEY = "codex:Fix the parser"

    def setUp(self):
        patcher = patch("chatgpt_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        self.ui = ChatGPTUI(worker)
        self.window = Window().said("user", "Zbuduj aplikację").said("bot", "Sprawdzam.").notice()
        self.ui.snapshot = lambda: self.window.root
        self.clicked, self.keys = [], []
        self.ui.click = lambda n: self.clicked.append(n.name)
        self.ui.type_into = lambda n, text: self.set_composer(text)

    def set_composer(self, text):
        self.window.prompt.control = composer(text)

    def press(self, keys, clears):
        self.keys.append(keys)
        if clears:
            self.set_composer("")

    def resume(self, enter_sends=True):
        with patch.object(gpt.app.auto, "SendKeys", lambda keys, **kw: self.press(keys, enter_sends)):
            return self.ui.resume(self.KEY)

    def test_message_is_typed_and_sent_with_enter(self):
        self.assertEqual(self.resume(), "message")
        self.assertEqual(self.keys, ["{Enter}"])
        self.assertEqual(self.clicked, [])

    def test_ctrl_enter_setting_falls_back_to_the_send_button(self):
        self.window.submit.name = "Wyślij"        # the composer holds text now
        self.ui.click = lambda n: (self.clicked.append(n.name), self.set_composer(""))
        self.assertEqual(self.resume(enter_sends=False), "message")
        self.assertEqual(self.clicked, ["Wyślij"])

    def test_message_that_cannot_be_sent_is_reported(self):
        self.window.submit.name = "Wyślij"
        with self.assertRaises(RuntimeError):
            self.resume(enter_sends=False)

    def test_message_refused_while_the_limit_is_on_is_taken_back(self):
        # 23.09: "Resume now" during the limit typed "continue", ChatGPT kept Send
        # disabled and the text stayed; that draft would have blocked every later resume.
        self.window.submit.name = "Wyślij"
        self.window.submit.control = types.SimpleNamespace(IsEnabled=False)
        self.ui.type_into = lambda n, text: setattr(self.window.prompt, "control", EditableComposer(text))
        with self.assertRaisesRegex(RuntimeError, "not accept"):
            self.resume(enter_sends=False)
        self.assertEqual(self.clicked, [])                 # the disabled Send is not clicked
        self.assertEqual(ChatGPTUI.value(self.window.prompt), "")

    def test_draft_and_busy_conversations_are_left_alone(self):
        self.set_composer("mój szkic")
        node("mój szkic", "TextControl", node(parent=self.window.prompt))
        with self.assertRaisesRegex(RuntimeError, "draft"):
            self.resume()
        self.set_composer("")
        self.window.submit.name = "Zatrzymaj"
        self.assertEqual(self.resume(), "busy")
        self.assertEqual(self.keys, [])


class MenuTests(unittest.TestCase):
    """The profile menu as captured on 21.09 after expanding "Pozostały limit"."""

    def menu(self, expanded=True):
        menu = node("Otwórz menu profilu", "MenuControl", role="menu")
        node("Jan Kowalski Plus", "MenuItemControl", menu)
        heading = node("Pozostały limit", "MenuItemControl", menu)
        node("Pozostały limit", "TextControl", heading)
        if expanded:
            for label, left, reset in (("5 godz.", "0%", "23:05"), ("Co tydzień", "62%", "28 wrz")):
                node(label, "TextControl", menu)
                node(left, "TextControl", menu)
                node(reset, "TextControl", node(parent=menu))
            node("Rozszerz do planu Pro", "MenuItemControl", menu)
        settings = node("Ustawienia Ctrl+,", "MenuItemControl", menu)
        node("Ustawienia", "TextControl", settings)
        return menu, heading

    def test_rows_are_read_after_the_heading_outside_menu_items(self):
        menu, heading = self.menu()
        self.assertEqual(gpt.ChatGPTWorker._menu_texts(menu, heading),
                         ["5 godz.", "0%", "23:05", "Co tydzień", "62%", "28 wrz"])

    def test_collapsed_heading_has_no_rows(self):
        menu, heading = self.menu(expanded=False)
        self.assertEqual(gpt.ChatGPTWorker._menu_texts(menu, heading), [])

    def worker_showing(self, *menus):
        """A worker whose snapshots show `menus[0]` until something is clicked, then `menus[1]`."""
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        roots = []
        for menu in menus:
            root = node("window", "WindowControl")
            menu.parent = root
            root.children.append(menu)
            roots.append(root)
        clicked = []
        worker.engine.ui.snapshot = lambda: roots[min(len(clicked), len(roots) - 1)]
        worker.engine.ui.click = clicked.append
        patcher = patch.object(gpt.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        return worker, clicked

    def test_collapsed_section_is_expanded_then_read(self):
        (collapsed, heading), (expanded, _) = self.menu(expanded=False), self.menu()
        worker, clicked = self.worker_showing(collapsed, expanded)
        rows = worker._usage_rows()
        self.assertEqual(clicked, [heading])
        self.assertEqual([(r["label"], r["left"]) for r in rows], [("5 godz.", 0), ("Co tydzień", 62)])

    def test_expanded_section_is_read_without_clicking(self):
        worker, clicked = self.worker_showing(self.menu()[0])
        self.assertEqual(len(worker._usage_rows()), 2)
        self.assertEqual(clicked, [])       # a click would fold the rows away

    def test_plain_usage_item_gives_the_percentage_only(self):
        menu = node("Otwórz menu profilu", "MenuControl", role="menu")
        node("Użycie Pozostało 0%", "MenuItemControl", menu)
        worker, clicked = self.worker_showing(menu)
        self.assertEqual(worker._usage_rows(), [dict(label="", minutes=None, left=0, reset=None, model=None)])
        self.assertEqual(clicked, [])       # that item opens Settings: never clicked

    def test_focus_goes_back_to_the_window_that_had_it(self):
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        worker.hwnd = 222
        root = node("window", "WindowControl")
        button = types.SimpleNamespace(IsOffscreen=False, IsEnabled=True, Click=Mock())
        node("Otwórz menu profilu", "ButtonControl", root, (8, 1354, 275, 1384), control=button)
        worker.engine.ui.snapshot = lambda: root
        focused = [111]                                   # the user's window is in front
        user32 = Mock(GetForegroundWindow=lambda: focused[-1], IsWindow=lambda h: True)
        worker._focus_window = focused.append
        worker._usage_rows = lambda: [dict(label="5 godz.", minutes=300, left=84, reset=None, model=None)]
        worker._close_menu = Mock()
        with patch.object(gpt, "user32", user32):
            rows, ok = worker._read_usage_menu(object())
        self.assertTrue(ok)
        button.Click.assert_called_once()
        self.assertEqual(focused, [111, 222, 111])        # ChatGPT for the check, then back

    def test_the_menu_input_is_marked_as_ours_before_it_goes_out(self):
        """Claude Auto-Resume reads the shared record at any moment: the click on the user
        button and the cursor put back must be ours already on their way."""
        worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        worker.hwnd = 222
        marks, sent = [], []
        worker.guard.own = types.SimpleNamespace(mark=lambda ahead_ms=0: marks.append(ahead_ms),
                                                 age_ms=lambda: 0)
        root = node("window", "WindowControl")
        button = types.SimpleNamespace(IsOffscreen=False, IsEnabled=True,
                                       Click=lambda **kw: sent.append(("click", list(marks))))
        node("Otwórz menu profilu", "ButtonControl", root, (8, 1354, 275, 1384), control=button)
        worker.engine.ui.snapshot = lambda: root
        user32 = Mock(GetForegroundWindow=lambda: 222, IsWindow=lambda h: True,
                      SetCursorPos=lambda x, y: sent.append(("cursor", list(marks))))
        worker._focus_window = lambda hwnd: None
        worker._usage_rows = lambda: []
        worker._close_menu = Mock()
        with patch.object(gpt, "user32", user32):
            worker._read_usage_menu(object())
        # each input goes out while a mark "ours for the next moment" (ahead > 0) is in force
        self.assertEqual([what for what, _ in sent], ["click", "cursor"])
        self.assertTrue(all(before and before[-1] > 0 for _, before in sent), sent)

    def test_plain_usage_item(self):
        self.assertEqual(gpt.USAGE_ITEM.match("Użycie Pozostało 0%").group(2), "0")
        self.assertEqual(gpt.USAGE_ITEM.match("Usage 40% left").group(1), "40")
        self.assertIsNone(gpt.USAGE_ITEM.match("Ustawienia Ctrl+,"))


class FixtureUI:
    def __init__(self, window):
        self.window, self.calls, self.clicks = window, [], 0

    def snapshot(self):
        return self.window.root

    def resolve(self, key, navigate=True):
        panes = [p for p in discover(self.window.root)[0] if p.key == key]
        return panes[0] if len(panes) == 1 else None

    def resume(self, key, prefer_retry, api_error, message=None):
        self.calls.append((key, prefer_retry, api_error))
        return "message"


class FakeLog:
    def __init__(self, reset=None):
        self.reset = reset

    def latest_reset(self):
        return self.reset


class EngineTests(unittest.TestCase):
    KEY = "codex:Fix the parser"

    def setUp(self):
        self.clock = [1000.0]
        patcher = patch("chatgpt_automation.time.monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.app.DEFAULT_CONFIG))
        self.worker.log = Mock()
        self.worker._get_window = Mock(return_value=object())
        self.window = Window().said("user", "Zbuduj aplikację").said("bot", "Sprawdzam.").notice()
        self.ui = FixtureUI(self.window)
        self.log = FakeLog()
        self.engine = ChatGPTEngine(self.worker, self.ui, quota=self.log)
        self.menu = []
        self.worker._read_usage_menu = Mock(side_effect=lambda win: (list(self.menu), bool(self.menu)))

    def scan(self, now, advance=0):
        self.scan_all(now, advance)
        return self.engine.sessions[self.KEY]

    def scan_all(self, now, advance=0):
        self.engine.next_scan = 0
        self.engine.tick(now=now)
        self.clock[0] += advance

    def rows(self, left, reset):
        return [dict(label="5h", minutes=300, left=left, reset=reset, model=None),
                dict(label="Weekly", minutes=10080, left=70, reset=dt.datetime(2026, 9, 28), model=None)]

    def test_used_up_window_schedules_after_its_reset(self):
        reset = dt.datetime(2026, 9, 21, 23, 5)
        self.menu = self.rows(0, reset)
        state = self.scan(NOW)
        self.assertEqual((state.phase, state.reason, state.reset), ("waiting", "limit", reset))
        self.assertEqual(state.due, reset + dt.timedelta(seconds=60))
        self.scan(reset + dt.timedelta(seconds=59))
        self.assertEqual(self.ui.calls, [])
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)   # no menu flashing while waiting
        self.scan(reset + dt.timedelta(seconds=60))
        self.assertEqual(self.ui.calls, [(self.KEY, False, False)])

    def test_limit_already_over_resumes_a_minute_later_at_a_fixed_time(self):
        self.menu = self.rows(100, dt.datetime(2026, 9, 22, 2, 39))    # rolling window, nothing used
        state = self.scan(NOW, advance=30)
        self.assertEqual((state.phase, state.reset), ("waiting", NOW))
        self.scan(NOW + dt.timedelta(seconds=30), advance=30)
        self.assertEqual(state.due, NOW + dt.timedelta(seconds=60))     # does not slide with the scans
        self.assertEqual(self.ui.calls, [])
        self.scan(NOW + dt.timedelta(seconds=60))
        self.assertEqual(len(self.ui.calls), 1)

    def test_menu_unreadable_uses_the_notice_time_or_a_future_log_reset(self):
        self.window = Window(lang="en").said("user", "x").said("bot", "y").notice(NOTICE_EN_AT)
        self.ui.window = self.window
        state = self.scan(NOW)
        self.assertEqual(state.reset, dt.datetime(2026, 9, 21, 23, 5))
        self.worker.log.assert_any_call("log_panel_unreadable", "warn")

    def test_past_log_reset_does_not_trigger_a_blind_send(self):
        self.log.reset = NOW - dt.timedelta(days=6)      # last week's rejection
        state = self.scan(NOW)
        self.assertEqual((state.phase, state.reset), ("waiting", None))
        self.assertEqual(self.ui.calls, [])
        self.log.reset = NOW + dt.timedelta(minutes=50)
        self.clock[0] += 400                               # panel backoff passed; menu still unreadable
        state = self.scan(NOW + dt.timedelta(minutes=5))
        self.assertEqual(state.reset, NOW + dt.timedelta(minutes=50))

    def test_menu_is_read_again_while_the_reset_is_unknown(self):
        self.scan(NOW)
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)
        self.scan(NOW + dt.timedelta(minutes=1))
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)    # backoff
        self.clock[0] += self.worker.cfg["panel_backoff_s"]
        self.menu = self.rows(0, dt.datetime(2026, 9, 21, 23, 5))
        state = self.scan(NOW + dt.timedelta(minutes=6))
        self.assertEqual(self.worker._read_usage_menu.call_count, 2)
        self.assertEqual(state.reset, dt.datetime(2026, 9, 21, 23, 5))

    def test_resumed_conversation_is_verified_and_cleared(self):
        self.menu = self.rows(100, None)
        self.scan(NOW)
        self.scan(NOW + dt.timedelta(seconds=61))
        self.assertEqual(len(self.ui.calls), 1)
        self.window.said("user", "continue").said("bot", "Kontynuuję.")
        state = self.scan(NOW + dt.timedelta(seconds=61 + self.worker.cfg["verify_delay_s"]))
        self.assertEqual(state.phase, "watching")
        self.assertNotIn(self.KEY, self.engine.cleared)

    def test_hidden_window_is_reported_once(self):
        self.ui.snapshot = Mock(side_effect=WindowHidden("covered"))
        self.worker.emit = Mock()
        self.engine.next_scan = 0
        self.engine.tick(now=NOW)
        self.engine.next_scan = 0
        self.engine.tick(now=NOW)
        self.worker.emit.assert_any_call("status", "hidden")
        self.assertEqual([c.args for c in self.worker.log.call_args_list].count(("log_hidden", "warn")), 1)
        self.assertEqual(self.worker._read_usage_menu.call_count, 0)     # nothing is clicked in a hidden window

    def working_window(self):
        self.window = Window().said("user", "Zbuduj aplikację").said("bot", "Sprawdzam.")
        self.ui.window = self.window

    def usage_events(self):
        return [c.args[1]["rows"] for c in self.worker.emit.call_args_list if c.args[0] == "usage"]

    def test_remaining_limit_is_checked_while_watching(self):
        """No limit on screen: "Usage remaining" is still read, then every usage_check_s."""
        self.working_window()
        self.worker.emit = Mock()
        self.menu = self.rows(84, dt.datetime(2026, 9, 21, 23, 5))
        self.scan(NOW)
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)
        self.assertEqual(self.usage_events()[-1]["5h"], dict(pct=16, left=84, reset=dt.datetime(2026, 9, 21, 23, 5)))
        self.assertEqual(self.usage_events()[-1]["weekly"]["left"], 70)
        self.scan(NOW + dt.timedelta(minutes=1))
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)
        self.clock[0] += self.worker.cfg.get("usage_check_s", gpt.USAGE_CHECK_S)
        self.menu = self.rows(61, dt.datetime(2026, 9, 21, 23, 5))
        self.scan(NOW + dt.timedelta(minutes=11))
        self.assertEqual(self.worker._read_usage_menu.call_count, 2)
        self.assertEqual(self.usage_events()[-1]["5h"]["left"], 61)
        self.assertEqual(self.engine.sessions[self.KEY].phase, "watching")    # a reading alone resumes nothing

    def test_remaining_limit_check_can_be_turned_off(self):
        self.working_window()
        self.worker.cfg["usage_check_s"] = 0
        self.menu = self.rows(84, None)
        self.scan(NOW)
        self.assertEqual(self.worker._read_usage_menu.call_count, 0)

    def test_limit_of_23_09_is_detected_and_resumed_after_the_reset(self):
        """Notice with the time, changed-files card and the limit card: waiting for 14:05."""
        self.window = (Window().said("user", "Uruchom projekt RECLAIM").said("bot", "Zapisuję raport.")
                       .notice(NOTICE_PL_AT).changed_files().banner())
        self.ui.window = self.window
        reset = dt.datetime(2026, 9, 23, 14, 5)
        self.menu = self.rows(0, reset)
        state = self.scan(NOW_2309)
        self.assertEqual((state.phase, state.reason, state.reset), ("waiting", "limit", reset))
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)    # one physical check, not two
        self.scan(reset + dt.timedelta(seconds=59))
        self.assertEqual(self.ui.calls, [])
        self.scan(reset + dt.timedelta(seconds=60))
        self.assertEqual(self.ui.calls, [(self.KEY, False, False)])

    def test_notice_followed_by_something_unknown_is_settled_by_the_menu_at_once(self):
        self.window = Window().said("user", "x").said("bot", "y").notice(NOTICE_PL_AT)
        node("Nieznany element", "TextControl", node(parent=self.window.feed))
        self.ui.window = self.window
        reset = dt.datetime(2026, 9, 23, 14, 5)
        self.menu = self.rows(0, reset)
        state = self.scan(NOW_2309)
        self.assertEqual((state.phase, state.reason, state.reset), ("waiting", "limit", reset))
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)    # one reading serves both checks

    def test_candidate_with_limit_left_does_not_open_the_menu_on_every_scan(self):
        self.window = Window().said("user", "x").said("bot", "y").notice(NOTICE_PL_AT)
        node("Nieznany element", "TextControl", node(parent=self.window.feed))
        self.ui.window = self.window
        self.menu = self.rows(40, dt.datetime(2026, 9, 23, 14, 5))
        self.assertEqual(self.scan(NOW_2309).phase, "watching")
        self.scan(NOW_2309 + dt.timedelta(seconds=20))
        self.assertEqual(self.worker._read_usage_menu.call_count, 1)
        self.clock[0] += self.worker.cfg["panel_backoff_s"]
        self.scan(NOW_2309 + dt.timedelta(minutes=5))
        self.assertEqual(self.worker._read_usage_menu.call_count, 2)
        self.assertEqual(self.ui.calls, [])

    def test_limit_in_the_newer_turn_layout_is_resumed_after_the_reset(self):
        """The 23.09 evening on the second PC: limit at 18:1x, reset 19:50, send at 19:51."""
        reset = dt.datetime(2026, 9, 23, 19, 50)
        self.window = Window().said("user", "Uruchom projekt RECLAIM").newer_turn()
        self.window.notice(NOTICE_PL_AT.replace("14:05", "19:50")).banner()
        self.ui.window = self.window
        self.menu = self.rows(0, reset)
        state = self.scan(dt.datetime(2026, 9, 23, 18, 21))
        self.assertEqual((state.phase, state.reset, state.due), ("waiting", reset, reset + dt.timedelta(minutes=1)))
        self.scan(dt.datetime(2026, 9, 23, 19, 50, 59))
        self.assertEqual(self.ui.calls, [])
        self.scan(dt.datetime(2026, 9, 23, 19, 51))
        self.assertEqual(self.ui.calls, [(self.KEY, False, False)])

    def logged(self, key):
        return [c for c in self.worker.log.call_args_list if c.args and c.args[0] == key]

    def test_missing_open_conversation_is_reported_once(self):
        self.window.title_button.name = "Nowy czat"       # e.g. the home page is showing
        self.scan_all(NOW)
        self.scan_all(NOW + dt.timedelta(seconds=20))
        self.assertEqual(len(self.logged("log_no_open_chat")), 1)
        self.window.title_button.name = "Fix the parser"
        self.scan(NOW + dt.timedelta(seconds=40))
        self.window.title_button.name = "Nowy czat"
        self.scan_all(NOW + dt.timedelta(seconds=60))
        self.assertEqual(len(self.logged("log_no_open_chat")), 2)

    def test_usage_log_names_the_windows_in_the_programs_language(self):
        """ChatGPT's own labels follow ChatGPT's language ("5 godz.", "Co tydzień"): the log
        names the windows itself, in the language of this program's window."""
        self.working_window()
        self.menu = [dict(label="5 godz.", minutes=300, left=84, reset=None, model=None),
                     dict(label="Co tydzień", minutes=10080, left=70, reset=None, model=None)]
        self.scan(NOW)
        (call,) = self.logged("log_usage_left")
        self.assertEqual(self.worker.t("log_usage_left", **call.kwargs),
                         "Usage remaining: 5-hour 84%, weekly 70%")
        self.worker.cfg["language"] = "pl"
        self.assertEqual(self.worker.t("log_usage_left", **call.kwargs),
                         "Pozostały limit: 5-godzinny 84%, tygodniowy 70%")

    def test_used_up_account_without_a_stop_notice_is_logged(self):
        """So that a notice the tool cannot see shows up in the log with what it saw."""
        self.working_window()
        self.menu = self.rows(0, dt.datetime(2026, 9, 21, 23, 5))
        self.scan(NOW)                                   # the periodic check finds the used-up window
        self.scan(NOW + dt.timedelta(seconds=20))
        actions = [c.kwargs["action"] for c in self.logged("log_chat_action")]
        self.assertTrue(any("Sprawdzam." in a for a in actions), actions)
        self.assertEqual(self.engine.sessions[self.KEY].phase, "watching")


class AppTests(unittest.TestCase):
    """The ChatGPT window is Claude Auto-Resume's window with ChatGPT's details."""

    def setUp(self):
        self.saved = []
        patches = [patch.object(gpt.app, "load_config",
                                lambda path=None, defaults=None: dict(defaults or gpt.app.DEFAULT_CONFIG)),
                   patch.object(gpt.app, "save_config", lambda cfg, path=None: self.saved.append((path, dict(cfg)))),
                   patch.object(gpt.ChatGPTWorker, "start", lambda w: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.ui = gpt.ChatGPTApp()
        self.ui.withdraw()
        self.addCleanup(self.ui.destroy)

    def test_source_column_fits_its_longest_label(self):
        """26.09: ChatGPT's "Open conversation" was cut to "Open conversatio"."""
        import tkinter.font as tkfont
        font = tkfont.Font(font=(self.ui.font_ui, 10))
        for lang in ("en", "pl"):
            self.ui._set_lang(lang)
            width = int(self.ui.tree_chats.column("source", "width"))
            for key in ("open", "sidebar", "col_source"):
                self.assertGreaterEqual(width, font.measure(self.ui._T(key)) + 16, (lang, key))

    def test_title_settings_file_and_options(self):
        self.assertEqual(self.ui.title(), "ChatGPT Auto-Resume")
        self.assertIsInstance(self.ui.worker, gpt.ChatGPTWorker)
        # No "Try again" button and no questions of its own in Codex, but the pause and
        # the handover work the same way in both programs.
        self.assertEqual(list(self.ui.feature_checks),
                         ["retry_api_errors", "pause_on_foreign_input", "handover_enabled"])
        self.ui._set_lang("pl")
        self.assertEqual(self.saved[-1][0], gpt.CONFIG_PATH)
        self.assertNotEqual(gpt.CONFIG_PATH, gpt.app.CONFIG_PATH)
        self.assertNotEqual(gpt.LOG_PATH, gpt.app.LOG_PATH)
        self.assertEqual(self.ui.lbl_window.cget("text"), "Okno ChatGPT:")

    def test_usage_shows_what_is_left(self):
        self.ui._set_lang("en")
        reset = dt.datetime(2026, 9, 21, 23, 5)
        self.ui._handle_event("usage", {"rows": {"5h": dict(pct=100, left=0, reset=reset),
                                                 "weekly": dict(pct=38, left=62)}})
        self.assertEqual(self.ui.lbl_used.cget("text"), "5-hour limit: 0% left")
        self.assertEqual(self.ui.lbl_plan.cget("text"), "weekly: 62% left")
        self.assertIn("23:05", self.ui.lbl_reset_seen.cget("text"))

    def test_usage_check_interval_is_a_setting_in_minutes(self):
        self.ui._set_lang("pl")
        self.assertEqual(self.ui.var_usage_check.get(), gpt.USAGE_CHECK_S // 60)
        self.assertIn("limit", self.ui.lbl_usage_check.cget("text"))
        self.ui.var_usage_check.set(5)
        self.ui._push_config()
        self.assertEqual(self.ui.cfg["usage_check_s"], 300)
        self.assertEqual(self.saved[-1][1]["usage_check_s"], 300)
        self.ui.var_usage_check.set(0)                 # 0 = only when a conversation hits the limit
        self.ui._push_config()
        self.assertEqual(self.ui.cfg["usage_check_s"], 0)

    def test_hidden_window_caption(self):
        self.ui._set_lang("en")
        self.ui._handle_event("state", dict(state="MONITORING", reset_at=None, send_at=None, start_until=None))
        self.ui._handle_event("status", "hidden")
        self.ui._update_caption()
        self.assertIn("ChatGPT window is covered", self.ui.lbl_caption.cget("text"))


class StringTests(unittest.TestCase):
    def test_chatgpt_texts_name_chatgpt_and_match_across_languages(self):
        self.assertEqual(set(gpt.STRINGS["en"]), set(gpt.STRINGS["pl"]))
        # One text may name Claude here: the pause reason says which app is driving the
        # screen, and when it is Claude, calling it ChatGPT would be a lie.
        names_claude = {"pause_claude_cu"}
        for lang in ("en", "pl"):
            for key, text in gpt.STRINGS[lang].items():
                if key in names_claude:
                    self.assertIn("Claude", text, (lang, key))
                    continue
                self.assertNotIn("Claude", text, (lang, key))
        self.assertIn("ChatGPT", gpt.app.tr("pl", "cap_no_window", table=gpt.STRINGS))
        self.assertIn("Claude", gpt.app.tr("pl", "cap_no_window"))     # Claude's own table untouched

    def test_chatgpt_sends_the_same_default_message(self):
        self.assertEqual(gpt.DEFAULT_CONFIG["message"], gpt.app.DEFAULT_MESSAGE)


if __name__ == "__main__":
    unittest.main()
