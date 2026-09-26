"""Keeps this PC out of the tests.

A real worker reads the real machine: when the mouse last moved, Claude Code's session
logs and ChatGPT's overlay windows. Its scheduler would then pause whenever someone - or
an agent, say the one running the tests - is at the computer, and its log lines would land
in the user's own log file. A test module that builds real workers starts with:

    def setUpModule():
        unittest.addModuleCleanup(calm_desk())
"""
import os
import shutil
import tempfile
from unittest.mock import patch

import chatgpt_auto_continue as gpt
import claude_auto_continue as app
import input_guard


def calm_desk():
    """Nobody at the mouse, no agent driving the screen, logs in a folder of their own.
    Returns the function that undoes it all."""
    folder = tempfile.mkdtemp()
    shared = "Local\\AutoResume.Test.OwnInput.%d" % os.getpid()      # not the running programs' record
    own_clock = input_guard.OwnInputClock
    patches = [patch.object(input_guard, "last_input_age_ms", lambda: 10 * 60 * 1000),
               patch.object(input_guard, "OwnInputClock", lambda name=None: own_clock(name or shared)),
               patch.object(input_guard.ClaudeComputerUse, "active", lambda self, hold_s: False),
               patch.object(input_guard, "overlay_visible", lambda skip_hwnd=None, phrases=None: False),
               patch.object(app, "LOG_PATH", os.path.join(folder, "auto_continue.log")),
               patch.object(gpt, "LOG_PATH", os.path.join(folder, "chatgpt_auto_continue.log"))]
    for patcher in patches:
        patcher.start()

    def undo():
        for patcher in reversed(patches):
            patcher.stop()
        shutil.rmtree(folder, ignore_errors=True)
    return undo
