"""A handed-over conversation keeps working where and how the old one did: the same
folder (and so its branch), model, effort and permission mode. Claude 2.37 trees as
measured on 15.10; no real window."""
import os
import queue
import shutil
import tempfile
import unittest
from unittest.mock import patch

import claude_auto_continue as app
import handover
from session_automation import ClaudeUI, Pane, chat_settings
from .test_handover import FlowUI
from .test_sessions import node, pane
from .test_support import calm_desk


def setUpModule():
    unittest.addModuleCleanup(calm_desk())


def worker():
    w = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
    w.log = lambda *a, **kw: None
    return w


class GitBranchTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, *parts, text):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_the_branch_of_a_repository(self):
        self.write("app", ".git", "HEAD", text="ref: refs/heads/feat/grupy-planow\n")
        self.assertEqual(handover.git_branch(os.path.join(self.root, "app")), "feat/grupy-planow")

    def test_a_subfolder_belongs_to_its_repository(self):
        self.write("app", ".git", "HEAD", text="ref: refs/heads/main\n")
        os.makedirs(os.path.join(self.root, "app", "src"))
        self.assertEqual(handover.git_branch(os.path.join(self.root, "app", "src")), "main")

    def test_a_worktree_points_to_its_own_head(self):
        # As Claude Code's worktrees: <repo>\.claude\worktrees\<name> with a .git file.
        gitdir = os.path.join(self.root, "app", ".git", "worktrees", "vigilant-moser")
        self.write("app", ".git", "worktrees", "vigilant-moser", "HEAD", text="ref: refs/heads/claude/vigilant-moser\n")
        self.write("app", ".claude", "worktrees", "vigilant-moser", ".git", text="gitdir: %s\n" % gitdir)
        folder = os.path.join(self.root, "app", ".claude", "worktrees", "vigilant-moser")
        self.assertEqual(handover.git_branch(folder), "claude/vigilant-moser")

    def test_a_detached_head_or_no_repository_has_no_branch(self):
        self.write("app", ".git", "HEAD", text="dcb3ae4a7f9e0e5b1c2d3e4f5a6b7c8d9e0f1a2b\n")
        self.assertIsNone(handover.git_branch(os.path.join(self.root, "app")))
        os.makedirs(os.path.join(self.root, "loose"))
        self.assertIsNone(handover.git_branch(os.path.join(self.root, "loose")))

    def test_the_new_conversation_is_told_where_it_works(self):
        t = lambda key, **kw: app.tr("en", key, **kw)
        message = handover.continue_message({}, t, r"C:\app\.handover\HANDOVER-1.md", "", r"C:\app", "main")
        self.assertIn(r"C:\app", message)
        self.assertIn("main", message)
        self.assertIn(r"C:\app", handover.continue_message({}, t, r"C:\app\.handover\HANDOVER-1.md", "", r"C:\app", None))


def composer_bar(parent, model=("Opus 5.5", "Max"), mode="Bypass permissions", effort_button=None):
    """The bar under a composer: the permission mode, then "Model: …" whose texts are the
    model and the effort (15.10). Older builds had "Effort: …" on a button of its own."""
    bar = node(parent=parent)
    node("Add", "ButtonControl", bar)
    node(mode, "ButtonControl", bar)
    button = node("Model: " + " ".join(model) + " 3.5× or more usage", "ButtonControl", bar)
    for text in model:
        node(text, "TextControl", button)
    if effort_button:
        node("Effort: " + effort_button, "ButtonControl", bar)
    node("Usage: Weekly · all models: 30%, Resets Mon 6:00 PM", "ButtonControl", bar)
    return bar


class ChatSettingsTests(unittest.TestCase):
    def test_model_effort_and_mode_of_a_conversation(self):
        p = pane("Obsługa wyczerpania tokenów")
        composer_bar(p.root)
        self.assertEqual(chat_settings(p.ui_nodes()), dict(model="Opus 5.5", effort="Max", mode="Bypass permissions"))

    def test_the_new_screen_names_the_mode_after_permissions(self):
        root = node()
        composer_bar(root, model=("Haiku 5.5", "Medium"), mode="Permissions: Manual")
        self.assertEqual(chat_settings(root.walk()), dict(model="Haiku 5.5", effort="Medium", mode="Manual"))

    def test_an_older_build_with_its_own_effort_button(self):
        root = node()
        bar = node(parent=root)
        node("Bypass permissions", "ButtonControl", bar)
        node("Model: Opus 5.5", "ButtonControl", bar)
        node("Effort: Extra", "ButtonControl", bar)
        self.assertEqual(chat_settings(root.walk()), dict(model="Opus 5.5", effort="Extra", mode="Bypass permissions"))

    def test_a_plan_button_elsewhere_is_not_the_mode(self):
        root = node()
        header = node(parent=root)
        plan = node("Plan", "ButtonControl", header)
        plan.rect = (10, 5, 60, 30)
        bar = composer_bar(root, mode="Manual")
        for n in bar.walk():
            n.rect = (10, 1350, 300, 1380)
        self.assertEqual(chat_settings(root.walk())["mode"], "Manual")


class NewScreen:
    """The 2.37 new-session screen with its menus: the permission modes, the models with
    "Effort" and "More models" (15.10). Clicks change it as the app does."""
    MODES = (("Auto", "Claude handles permission decisions"), ("Manual", "Always ask before making changes"),
             ("Accept edits", "Automatically accept all file edits"), ("Plan", "Create a plan before making changes"),
             ("Bypass permissions", "Claude can modify or delete files without asking"))
    MODELS = ("Opus 5.5", "Sonnet 5.5", "Fable 5.1 Requires usage credits", "Haiku 5.5")
    MORE = ("Haiku 4.5", "Sonnet 5", "Opus 5")
    EFFORTS = (("Low",), ("Medium", "Recommended"), ("High",), ("Extra",), ("Max", "3.5× or more usage"), ("Ultracode",))

    def __init__(self, mode="Bypass permissions", model="Opus 5.5", effort="Extra", missing=()):
        self.mode, self.model, self.effort, self.missing = mode, model, effort, set(missing)
        self.open = []
        self.clicked = []

    def root(self):
        root = node()
        screen = node(parent=root)
        node("Prompt", "EditControl", screen)
        node("Environment: auto-resume", "ButtonControl", screen)
        self.mode_button = node("Permissions: " + self.mode, "ButtonControl", screen)
        self.model_button = node("Model: %s %s" % (self.model, self.effort), "ButtonControl", screen)
        node(self.model, "TextControl", self.model_button)
        node(self.effort, "TextControl", self.model_button)
        self.items = {}
        for name in self.open:
            menu = node(name, "MenuControl", root)
            menu.role = "menu"
            for label, *rest in self.entries(name):
                kind = "MenuItemControl" if label in ("Effort " + self.effort, "More models") else "RadioButtonControl"
                item = node(" ".join([label, *rest]), kind, menu)
                for text in [label, *rest]:
                    node(text, "TextControl", item)
                self.items[item.name] = (name, label)
        return root

    def entries(self, menu):
        if menu == "permissions":
            return self.MODES
        if menu == "model":
            return [(m,) for m in self.MODELS if m.split(" Requires")[0] not in self.missing] + \
                   [("Effort " + self.effort,), ("More models",)]
        if menu == "effort":
            return [e for e in self.EFFORTS if e[0] not in self.missing]
        return [(m,) for m in self.MORE]

    def click(self, n):
        self.clicked.append(n.name)
        if n.name.startswith("Permissions:"):
            self.open = ["permissions"]
        elif n.name.startswith("Model:"):
            self.open = ["model"]
        elif n.name in self.items:
            menu, label = self.items[n.name]
            if label.startswith("Effort "):
                self.open.append("effort")
            elif label == "More models":
                self.open.append("more")
            else:
                if menu == "permissions":
                    self.mode = label
                elif menu == "effort":
                    self.effort = label
                else:
                    self.model = label.split(" Requires")[0]
                self.open = []

    def press(self, keys, wait=0.1):
        self.clicked.append(keys)
        if self.open:
            self.open.pop()


class ApplySettingsTests(unittest.TestCase):
    def setUp(self):
        self.ui = ClaudeUI(worker())
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def wire(self, screen):
        self.screen = screen
        self.ui.snapshot = screen.root
        self.ui.click = screen.click
        self.ui.press = screen.press
        self.ui.panes = lambda root=None: []

    def test_mode_model_and_effort_are_set(self):
        self.wire(NewScreen(mode="Bypass permissions", model="Opus 5.5", effort="Extra"))
        missed = self.ui.apply_settings(dict(model="Sonnet 5.5", effort="Max", mode="Manual"))
        self.assertEqual(missed, [])
        self.assertEqual((self.screen.mode, self.screen.model, self.screen.effort), ("Manual", "Sonnet 5.5", "Max"))

    def test_what_already_matches_is_not_touched(self):
        self.wire(NewScreen())
        self.assertEqual(self.ui.apply_settings(dict(model="Opus 5.5", effort="Extra", mode="Bypass permissions")), [])
        self.assertEqual(self.screen.clicked, [])

    def test_an_older_model_is_found_under_more_models(self):
        self.wire(NewScreen())
        self.assertEqual(self.ui.apply_settings(dict(model="Opus 5", effort="", mode="")), [])
        self.assertEqual(self.screen.model, "Opus 5")

    def test_a_model_not_on_the_list_is_reported_and_the_rest_still_set(self):
        self.wire(NewScreen(missing=("Fable 5.1",)))
        missed = self.ui.apply_settings(dict(model="Fable 5.1", effort="High", mode="Plan"))
        self.assertEqual(missed, ["model"])
        self.assertEqual((self.screen.mode, self.screen.effort), ("Plan", "High"))
        self.assertEqual(self.screen.open, [])           # no menu left open behind us

    def test_a_folder_named_like_the_start_of_another_is_not_confused(self):
        menu = node()
        top = node("Environment: auto-resume", "MenuControl", menu)
        top.role = "menu"
        item = node("prep it , edit environment, right arrow", "RadioButtonControl", top)
        node("prep it", "TextControl", item)
        self.ui.snapshot = lambda: menu
        self.ui.click = lambda n: None
        self.ui.press = lambda keys, wait=0.1: None
        self.assertFalse(self.ui.pick_project(node("Environment: auto-resume", "ButtonControl"), "prep"))


class NewChatSettingsTests(unittest.TestCase):
    """new_chat reads the old conversation's settings and gives them to the new one."""

    def setUp(self):
        self.ui = ClaudeUI(worker())
        self.old = pane("Alpha")
        composer_bar(self.old.root, model=("Sonnet 5.5", "High"), mode="Accept edits")
        sidebar = node("Sidebar", parent=self.old.root)
        node("New", "ButtonControl", sidebar)
        self.screen = NewScreen()
        self.project = os.path.join(tempfile.mkdtemp(), "auto-resume")
        os.makedirs(os.path.join(self.project, ".git"))
        with open(os.path.join(self.project, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/feat/handover\n")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.project))
        self.sent = []
        self.ui.resolve = lambda key, navigate=True: self.old
        self.ui.value = lambda n: ""
        self.ui.panes = lambda root=None: [self.old] if root is self.old.root else []
        self.ui.send = lambda pane_, text: self.sent.append(text)
        self.ui.settled_key = lambda before, wait_s=None: "code:Nowa rozmowa"
        reads = {"n": 0}

        def snapshot():
            reads["n"] += 1
            return self.old.root if reads["n"] == 1 else self.screen.root()
        self.ui.snapshot = snapshot
        self.ui.click = lambda n: self.screen.click(n) if n.name != "New" and n is not self.old.prompt else None
        self.ui.press = self.screen.press
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_new_conversation_gets_the_old_ones_settings(self):
        key = self.ui.new_chat("code:Alpha", self.project, "przejmij pracę")
        self.assertEqual(key, "code:Nowa rozmowa")
        self.assertEqual((self.screen.mode, self.screen.model, self.screen.effort), ("Accept edits", "Sonnet 5.5", "High"))
        self.assertEqual(self.sent, ["przejmij pracę"])
        self.assertEqual(self.ui.last_settings, dict(folder=self.project, branch="feat/handover", model="Sonnet 5.5",
                                                     effort="High", mode="Accept edits", missed=[]))

    def test_a_setting_that_cannot_be_set_does_not_stop_the_handover(self):
        self.screen.missing = {"Sonnet 5.5"}
        self.ui.new_chat("code:Alpha", self.project, "przejmij pracę")
        self.assertEqual(self.sent, ["przejmij pracę"])
        self.assertEqual(self.ui.last_settings["missed"], ["model"])


class FlowLogTests(unittest.TestCase):
    """The log says what the new conversation got, and what it could not get."""

    def test_the_settings_are_logged_and_the_missing_ones_warned_about(self):
        import datetime as dt
        from session_automation import SessionEngine, SessionState
        project = os.path.join(tempfile.mkdtemp(), "app")
        self.addCleanup(shutil.rmtree, os.path.dirname(project))
        os.makedirs(os.path.join(project, ".git"))
        with open(os.path.join(project, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")
        w = worker()
        logged = []
        w.log = lambda key, level="info", **kw: logged.append((key, level, kw))
        ui = FlowUI()
        ui.last_settings = dict(folder=project, branch="main", model="Opus 5.5", effort="Max",
                                mode="Bypass permissions", missed=["effort"])
        engine = SessionEngine(w, ui)
        flow = engine.handover
        state = flow.state_for("code:Alpha")
        state.path, state.phase = os.path.join(project, ".handover", "HANDOVER-20261015-080000.md"), "ready"
        flow._open_new("code:Alpha", SessionState(), state, dt.datetime(2026, 10, 15, 8, 0))
        keys = [k for k, level, kw in logged]
        self.assertIn("log_handover_settings", keys)
        settings = next(kw for k, level, kw in logged if k == "log_handover_settings")
        self.assertIn("Opus 5.5", str(settings["settings"]))
        missed = next((level, kw) for k, level, kw in logged if k == "log_handover_missed")
        self.assertEqual(missed[0], "warn")
        message = next(c for c in ui.calls if c[0] == "new_chat")[3]
        self.assertIn("main", message)


if __name__ == "__main__":
    unittest.main()
