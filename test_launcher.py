"""The Main.bat picker: three tiles, what each starts, and window placement."""
import importlib.util
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import launcher


class PlacementTests(unittest.TestCase):
    def test_two_auto_resumes_side_by_side_on_a_wide_screen(self):
        self.assertEqual(launcher.side_by_side((0, 0, 2560, 1392)), ("+412+216", "+1288+216"))

    def test_narrow_screen_cascades_instead_of_stacking(self):
        left, right = launcher.side_by_side((0, 0, 1366, 728))
        x_left, x_right = int(left.split("+")[1]), int(right.split("+")[1])
        self.assertGreaterEqual(x_right, x_left + 60)

    def test_covered_window_detection(self):
        maximized = (-8, -8, 2568, 1400)
        self.assertTrue(launcher.covers(maximized, (0, 0, 2560, 1392)))
        self.assertTrue(launcher.covers((0, 0, 2560, 1392), maximized))       # within the slack
        self.assertFalse(launcher.covers((0, 0, 1280, 1392), (131, 0, 1425, 1399)))

    def test_a_click_through_overlay_is_never_taken_for_the_app_window(self):
        """While Claude drives the computer it draws a transparent overlay over the whole
        screen and shrinks its own window to a panel: the panel is the window to watch."""
        windows = [("claude", 11, 2560 * 1440, True),       # the overlay (WS_EX_TRANSPARENT)
                   ("claude", 12, 536 * 728, False),        # Claude itself
                   ("chatgpt", 21, 1280 * 1392, False)]
        self.assertEqual(launcher.pick_main_windows(windows), {"claude": 12, "chatgpt": 21})

    def test_a_minimized_app_is_brought_back_for_its_tile(self):
        """A minimized window shows screen readers an empty page: watching needs it on screen."""
        shown = []
        with patch.object(launcher, "main_windows", return_value={"claude": 1, "chatgpt": 2}), \
                patch.object(launcher, "is_minimized", side_effect=lambda hwnd: hwnd == 2), \
                patch.object(launcher, "show_without_focus", side_effect=shown.append):
            self.assertEqual(launcher.show_apps(("chatgpt",)), ["chatgpt"])
            self.assertEqual(launcher.show_apps(("claude",)), [])
            self.assertEqual(launcher.show_apps(("claude", "chatgpt")), ["chatgpt"])
        self.assertEqual(shown, [2, 2])

    def test_arranging_only_when_one_app_hides_the_other(self):
        with patch.object(launcher, "main_windows", return_value={"claude": 1, "chatgpt": 2}), \
                patch.object(launcher, "_rect", side_effect=lambda h: (0, 0, 1280, 1392) if h == 1
                             else (131, 0, 1425, 1399)):
            self.assertFalse(launcher.arrange_apps())
        with patch.object(launcher, "main_windows", return_value={"claude": 1}):
            self.assertFalse(launcher.arrange_apps())


class LaunchTests(unittest.TestCase):
    def test_tiles_and_what_they_start(self):
        self.assertEqual(launcher.HEADLINE, "Pick which auto resume you want to use")
        tiles = {t["key"]: t for t in launcher.TILES}
        self.assertEqual([t["title"] for t in launcher.TILES],
                         ["ChatGPT", "Claude", "My brain has no wrinkles left"])
        self.assertEqual(tiles["both"]["subtitle"], "(launch both and use both)")
        self.assertEqual(tiles["chatgpt"]["scripts"], ("chatgpt_auto_continue.py",))
        self.assertEqual(tiles["claude"]["scripts"], ("claude_auto_continue.py",))
        for tile in launcher.TILES:
            for script in tile["scripts"]:
                self.assertTrue(os.path.isfile(os.path.join(launcher.APP_DIR, script)), script)

    def test_both_start_side_by_side_claude_left(self):
        calls = []
        with patch.object(launcher, "launch", side_effect=lambda s, a=(): calls.append((s, a))), \
                patch.object(launcher, "running_window", return_value=0), \
                patch.object(launcher, "work_area", return_value=(0, 0, 2560, 1392)):
            launcher.launch_tile(launcher.TILES[2])
            launcher.launch_tile(launcher.TILES[0])
        self.assertEqual(calls, [("claude_auto_continue.py", ("--geometry", "+412+216")),
                                 ("chatgpt_auto_continue.py", ("--geometry", "+1288+216")),
                                 ("chatgpt_auto_continue.py", ())])

    def test_a_program_already_open_is_brought_forward_not_started_again(self):
        """Clicking a tile twice must not open a second copy watching the same window."""
        calls, forward = [], []
        with patch.object(launcher, "launch", side_effect=lambda s, a=(): calls.append((s, a))), \
                patch.object(launcher, "running_window",
                             side_effect=lambda script: 55 if script == "claude_auto_continue.py" else 0), \
                patch.object(launcher, "bring_forward", side_effect=forward.append), \
                patch.object(launcher, "work_area", return_value=(0, 0, 2560, 1392)):
            launcher.launch_tile(launcher.TILES[2])
            launcher.launch_tile(launcher.TILES[1])
        self.assertEqual(calls, [("chatgpt_auto_continue.py", ("--geometry", "+1288+216"))])
        self.assertEqual(forward, [55, 55])

    def test_gui_programs_start_without_a_console(self):
        with patch.object(launcher.sys, "executable", r"C:\Python312\python.exe"), \
                patch.object(launcher.os.path, "isfile", return_value=True):
            self.assertEqual(launcher.gui_python(), r"C:\Python312\pythonw.exe")


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.window = launcher.Launcher()
        self.window.withdraw()
        self.addCleanup(lambda: self.window.winfo_exists() and self.window.destroy())

    def test_three_tiles_with_pictures(self):
        self.assertEqual(len(self.window.tiles), 3)
        self.assertEqual([e["title"].cget("text") for e in self.window.tiles],
                         ["ChatGPT", "Claude", "My brain has no wrinkles left"])
        self.assertEqual(set(self.window.images), {"chatgpt", "claude", "both"})

    def test_tile_starts_its_program_and_closes_the_picker(self):
        with patch.object(launcher, "launch_tile") as start, patch.object(launcher, "arrange_apps", return_value=False):
            self.window._activate(1)
        start.assert_called_once_with(launcher.TILES[1])
        self.assertTrue(self.window.busy)
        self.window._activate(0)                          # a second click is ignored while starting
        start.assert_called_once()

    def test_the_picker_says_when_it_brought_an_app_back(self):
        with patch.object(launcher, "launch_tile"), patch.object(launcher, "arrange_apps", return_value=False), \
                patch.object(launcher, "show_apps", return_value=["chatgpt"]) as show:
            self.window._activate(2)                          # both
        show.assert_called_once_with(("claude", "chatgpt"))
        self.assertIn("ChatGPT", self.window.status.cget("text"))
        self.assertIn("minimized", self.window.status.cget("text"))

    def test_failed_start_keeps_the_picker_open_with_the_reason(self):
        with patch.object(launcher, "launch_tile", side_effect=OSError("pythonw.exe not found")):
            self.window._activate(0)
        self.assertFalse(self.window.busy)
        self.assertIn("pythonw.exe not found", self.window.status.cget("text"))


class PictureTests(unittest.TestCase):
    """The tile pictures ship with the program, so every copy shows them: on a PC without
    the ChatGPT or Claude app, and without Pillow."""

    def open_window(self):
        window = launcher.Launcher()
        window.withdraw()
        self.addCleanup(window.destroy)
        return window

    def test_all_three_pictures_are_files_of_the_program(self):
        for key in ("chatgpt", "claude", "both"):
            path = launcher.icon_path(key)
            self.assertEqual(os.path.dirname(path), launcher.ASSETS)
            self.assertTrue(os.path.isfile(path), path)

    def test_pictures_load_on_a_pc_without_the_apps(self):
        with patch("winreg.OpenKey", side_effect=OSError("no Store packages here")):
            window = self.open_window()
        self.assertEqual(set(window.images), {"chatgpt", "claude", "both"})

    def test_pictures_load_without_pillow_at_about_the_tile_size(self):
        with patch.dict(sys.modules, {"PIL": None}):          # "import PIL" fails
            window = self.open_window()
        size = window.px(launcher.Launcher.ICON_PX)
        for key in ("chatgpt", "claude", "both"):
            image = window.images[key]
            self.assertIsInstance(image, launcher.tk.PhotoImage)
            self.assertTrue(0.75 * size <= image.width() <= 1.1 * size, (key, image.width(), size))

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_git_keeps_the_pictures(self):
        for key in ("chatgpt", "claude", "both"):
            ignored = subprocess.run(["git", "check-ignore", "-q", launcher.icon_path(key)],
                                     cwd=launcher.APP_DIR, capture_output=True)
            self.assertEqual(ignored.returncode, 1, key)          # 1: not ignored
        with open(os.path.join(launcher.APP_DIR, ".gitattributes"), encoding="utf-8") as f:
            self.assertIn("*.png binary", f.read())


@unittest.skipUnless(importlib.util.find_spec("PIL"), "the icon tool needs Pillow")
class IconToolTests(unittest.TestCase):
    """tools/make_app_icons.py, which drew the ChatGPT and Claude pictures."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "make_app_icons", os.path.join(launcher.APP_DIR, "tools", "make_app_icons.py"))
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_package_versions_sort_numerically(self):
        self.assertGreater(self.tool._version("OpenAI.Codex_26.915.4065.0_x64__2p2nqsd0c76g0"),
                           self.tool._version("OpenAI.Codex_26.99.9999.0_x64__2p2nqsd0c76g0"))
        self.assertEqual(self.tool._version("broken"), ())


if __name__ == "__main__":
    unittest.main()
