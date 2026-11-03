"""ChatGPT after its October update (measured 14.10): the open conversation's title moved
out of the main area, two "Nowy czat" buttons, a project list that names the folder under
the project, and what the new-conversation screen lets a handover set. No real window."""
import os
import queue
import shutil
import tempfile
import unittest
from unittest.mock import patch

import chatgpt_auto_continue as gpt
import handover
from chatgpt_automation import discover, gpt_settings, signals
from .test_chatgpt import NOW, Window, composer, node
from .test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


class TitleTests(unittest.TestCase):
    def test_the_title_above_the_conversation_names_it(self):
        """14.10: no title button in the main area any more; the title is a text in the
        window's top bar, beside the main area."""
        w = Window(title="Dodaj wspólne auto resume", chats=("Dodaj wspólne auto resume", "Inny czat"))
        w.title_button.parent.children.remove(w.title_button)
        node("Dodaj wspólne auto resume", "TextControl", w.doc, (400, 52, 700, 70))
        panes, _ = discover(w.root)
        self.assertEqual([p.key for p in panes], ["codex:Dodaj wspólne auto resume"])

    def test_the_notice_is_read_with_the_title_in_the_top_bar(self):
        """Without a title button in the main area the conversation is read from the top
        of the main area, so the notice that ends it is still a limit."""
        w = Window(title="Dodaj wspólne auto resume", chats=("Dodaj wspólne auto resume", "Inny czat"))
        w.title_button.parent.children.remove(w.title_button)
        node("Dodaj wspólne auto resume", "TextControl", w.doc, (400, 52, 700, 70))
        w.said("user", "Zbuduj aplikację").said("bot", "Sprawdzam kod.").notice()
        s = signals(discover(w.root)[0][0], NOW)
        self.assertEqual((s["limit"], s["reset"], s["busy"]), (True, None, False))

    def test_the_new_conversation_screen_is_not_a_conversation_named_after_the_app(self):
        w = Window(title="ChatGPT", chats=("Inny czat",))
        w.title_button.parent.children.remove(w.title_button)
        node("ChatGPT", "TextControl", w.doc, (400, 52, 700, 70))
        self.assertEqual(discover(w.root)[0], [])

    def test_a_sidebar_row_alone_does_not_name_the_open_conversation(self):
        w = Window(title="Dodaj wspólne auto resume", chats=("Dodaj wspólne auto resume",))
        w.title_button.parent.children.remove(w.title_button)
        self.assertEqual(discover(w.root)[0], [])


class Screen:
    """ChatGPT's new-conversation screen (14.10) with its menus; clicks change it as the
    app does. The project list shows "tnw endorfina" for the folder "aplikacja fitness"."""
    PROJECTS = (("auto-resume",), ("tnw endorfina", "aplikacja fitness"), ("Interactive Flow Chart",),
                ("Nowy projekt",), ("Pracuj bez projektu",))
    PERMISSIONS = (("Poproś o zatwierdzenie", "Zawsze pytaj o zgodę"), ("Zatwierdzaj za mnie", "Pytaj tylko przy ryzyku"),
                   ("Pełny dostęp", "Nieograniczony dostęp"))
    WHERE = ("Lokalny", "Nowe lokalne drzewo robocze", "Chmura")
    MODELS = ("6 Luna", "5.6 Terra", "5.6 Luna")

    def __init__(self, project="auto-resume", where="Lokalny", branch="main", branches=("main",),
                 permission="Pełny dostęp", model="6 Luna Wysoki"):
        self.project, self.where, self.branch, self.branches = project, where, branch, branches
        self.permission, self.model = permission, model
        self.open, self.clicked, self.items = [], [], {}

    def root(self):
        root = node("window", "WindowControl")
        doc = node("ChatGPT", "DocumentControl", root, (0, 0, 2560, 1392), role="document")
        side = node(parent=doc, rect=(0, 36, 323, 1392), role="navigation")
        node("Nowy czat", "ButtonControl", side, (60, 96, 287, 126))
        group = node(parent=side)
        node("Opcje paska bocznego tryb", "ButtonControl", group, (300, 493, 324, 517))
        node("Nowy czat", "ButtonControl", group, (327, 493, 351, 517))
        main = node(parent=doc, rect=(323, 36, 2560, 1392), role="main")
        row = node(parent=main, rect=(1080, 1240, 1800, 1270))
        self.project_button = node("Zmień projekt: " + self.project, "ButtonControl", row, (1089, 1241, 1203, 1270))
        node(self.project, "TextControl", self.project_button, (1120, 1246, 1194, 1265))
        node("Pracuj bez projektu", "ButtonControl", row, (1089, 1241, 1117, 1270))
        self.where_button = node("Wybierz, gdzie uruchomić czat", "ButtonControl", row, (1210, 1241, 1294, 1270))
        node(self.where, "TextControl", self.where_button, (1241, 1246, 1285, 1265))
        self.branch_button = node("Przełącz gałąź", "ButtonControl", row, (1302, 1241, 1371, 1270))
        node(self.branch, "TextControl", self.branch_button, (1333, 1245, 1362, 1265))
        box = node(parent=main, rect=(1080, 1280, 1800, 1380))
        node("Zleć cokolwiek", "EditControl", box, (1084, 1288, 1796, 1332), role="textbox", control=composer(""))
        node("Dodaj pliki i nie tylko", "ButtonControl", box, (1080, 1336, 1108, 1364))
        self.permission_button = node("Zmień uprawnienia", "ButtonControl", box, (1113, 1336, 1222, 1364))
        node(self.permission, "TextControl", self.permission_button, (1140, 1341, 1215, 1360))
        node("Przejrzyj hooki", "ButtonControl", box, (1226, 1336, 1255, 1364))
        self.model_button = node(self.model, "ButtonControl", box, (1614, 1336, 1736, 1364))
        node("Dyktuj", "ButtonControl", box, (1736, 1336, 1764, 1364))
        node("Wyślij", "ButtonControl", box, (1772, 1336, 1800, 1364))
        self.items = {}
        for name in self.open:
            menu = node(name, "ListControl" if name == "projects" else "MenuControl", root,
                        role="listbox" if name == "projects" else "menu")
            for kind, texts in self.entries(name):
                item = node(" ".join(texts), kind, menu)
                for text in texts:
                    node(text, "TextControl", item)
                self.items[item.name] = (name, texts[0])
        return root

    def entries(self, menu):
        if menu == "projects":
            return [("ListItemControl", p) for p in self.PROJECTS]
        if menu == "permissions":
            return [("MenuItemControl", p) for p in self.PERMISSIONS]
        if menu == "where":
            return [("MenuItemControl", (w,)) for w in self.WHERE]
        if menu == "branches":
            return [("MenuItemControl", (b, "Niezatwierdzone: 2 pliki")) for b in self.branches]
        if menu == "model":
            return [("MenuItemControl", ("Wybierz model",)), ("MenuItemControl", ("Przywróć domyślne",)),
                    ("MenuItemControl", ("Moc",))]
        return [("RadioButtonControl", (m,)) for m in self.MODELS]

    def click(self, n):
        self.clicked.append(n.name)
        name = n.name
        if name.startswith("Zmień projekt"):
            self.open = ["projects"]
        elif name == "Zmień uprawnienia":
            self.open = ["permissions"]
        elif name == "Wybierz, gdzie uruchomić czat":
            self.open = ["where"]
        elif name == "Przełącz gałąź":
            self.open = ["branches"]
        elif n is self.model_button or name == self.model:
            self.open = ["model"]
        elif name in self.items:
            menu, label = self.items[name]
            if label == "Wybierz model":
                self.open.append("models")
                return
            if menu == "projects":
                self.project = label
            elif menu == "permissions":
                self.permission = label
            elif menu == "where":
                self.where = label
            elif menu == "branches":
                self.branch = label
            elif menu == "models":
                effort = self.model.split(" ")[-1]
                self.model = label + " " + effort
            self.open = []

    def press(self, keys, wait=0.1):
        self.clicked.append(keys)
        if self.open:
            self.open.pop()


class NewChatTests(unittest.TestCase):
    def setUp(self):
        self.worker = gpt.ChatGPTWorker(queue.Queue(), dict(gpt.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = self.worker.engine.ui
        self.old = Window(title="Stary czat", chats=("Stary czat",))
        box = self.old.box
        permission = node("Zmień uprawnienia", "ButtonControl", box, (1113, 1336, 1222, 1364))
        node("Zatwierdzaj za mnie", "TextControl", permission)
        node("5.6 Terra Wysoki", "ButtonControl", box, (1614, 1336, 1736, 1364))
        wide = next(n for n in self.old.root.walk() if n.name == "Nowy czat")
        node("Nowy czat", "ButtonControl", wide.parent, (327, 493, 351, 517))     # the small one beside a project
        self.folder = os.path.join(tempfile.mkdtemp(), "aplikacja fitness")
        os.makedirs(os.path.join(self.folder, ".git"))
        self.addCleanup(shutil.rmtree, os.path.dirname(self.folder))
        self.set_branch("main")
        self.screen = Screen()
        self.sent = []
        reads = {"n": 0}

        def snapshot():
            reads["n"] += 1
            return self.old.root if reads["n"] == 1 else self.screen.root()
        self.ui.snapshot = snapshot
        self.ui.resolve = lambda key, navigate=True: discover(self.old.root)[0][0]
        self.ui.click = lambda n: None if n.name == "Nowy czat" else self.screen.click(n)
        self.ui.press = self.screen.press
        self.ui.send = lambda pane_, text: self.sent.append(text)
        self.ui.settled_key = lambda before, wait_s=None: "codex:Nowy czat"
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("chatgpt_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_branch(self, branch):
        with open(os.path.join(self.folder, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/%s\n" % branch)

    def test_the_old_conversations_settings_are_read_from_its_composer(self):
        pane = discover(self.old.root)[0][0]
        self.assertEqual(gpt_settings(pane), dict(model="5.6 Terra Wysoki", effort="", mode="Zatwierdzaj za mnie"))

    def test_the_project_is_found_by_its_folder_and_the_settings_follow(self):
        key = self.ui.new_chat("codex:Stary czat", self.folder, "przejmij pracę")
        self.assertEqual(key, "codex:Nowy czat")
        self.assertEqual(self.screen.project, "tnw endorfina")
        self.assertEqual((self.screen.permission, self.screen.model), ("Zatwierdzaj za mnie", "5.6 Terra Wysoki"))
        self.assertEqual(self.sent, ["przejmij pracę"])
        self.assertEqual(self.ui.last_settings["missed"], [])
        self.assertEqual(self.ui.last_settings["branch"], "main")

    def test_an_effort_that_differs_is_reported_not_guessed(self):
        self.screen.model = "5.6 Terra Niski"
        self.ui.new_chat("codex:Stary czat", self.folder, "przejmij pracę")
        self.assertEqual(self.ui.last_settings["missed"], ["effort"])
        self.assertEqual(self.sent, ["przejmij pracę"])

    def test_a_chat_meant_for_the_cloud_is_brought_back_to_this_computer(self):
        self.screen.where = "Chmura"
        self.ui.new_chat("codex:Stary czat", self.folder, "przejmij pracę")
        self.assertEqual(self.screen.where, "Lokalny")

    def test_the_branch_of_the_folder_is_chosen(self):
        self.set_branch("feat/grupy-planow")
        self.screen.branches = ("main", "feat/grupy-planow")
        self.ui.new_chat("codex:Stary czat", self.folder, "przejmij pracę")
        self.assertEqual(self.screen.branch, "feat/grupy-planow")
        self.assertEqual(self.ui.last_settings["branch"], "feat/grupy-planow")

    def test_a_branch_that_cannot_be_chosen_stops_the_handover(self):
        self.set_branch("feat/nieznana")
        with self.assertRaises(handover.HandoverRefused):
            self.ui.new_chat("codex:Stary czat", self.folder, "przejmij pracę")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.screen.open, [])

    def test_a_folder_without_a_project_stops_the_handover(self):
        missing = os.path.join(os.path.dirname(self.folder), "inny folder")
        os.makedirs(missing)
        with self.assertRaises(handover.HandoverRefused):
            self.ui.new_chat("codex:Stary czat", missing, "przejmij pracę")
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
