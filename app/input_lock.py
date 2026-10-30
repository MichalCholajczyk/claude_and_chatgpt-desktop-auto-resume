"""One Auto-Resume at a time drives the mouse and keyboard.

Claude and ChatGPT Auto-Resume run as separate programs. Every action focuses the
app window, clicks, types and presses Enter; if the other program brought its own
window forward in the middle, the keystrokes would land in the wrong app. A named
mutex (per Windows session) serializes those sequences across both programs.
"""
import contextlib
import ctypes
import time

NAME = "Local\\AutoResume.InputLock"
WAIT_OBJECT_0, WAIT_ABANDONED = 0x0, 0x80
DEFAULT_TIMEOUT_S = 45.0


class InputBusy(RuntimeError):
    """The other Auto-Resume kept the keyboard for too long; try on a later scan."""


@contextlib.contextmanager
def input_lock(cancelled=lambda: False, timeout=DEFAULT_TIMEOUT_S):
    """Hold the shared input lock. Windows mutexes are re-entrant for their
    owning thread, so nested use inside one worker is safe."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, NAME)
    if not handle:            # no lock object at all: acting beats never acting
        yield
        return
    try:
        deadline = time.monotonic() + timeout
        while True:
            # WAIT_ABANDONED: the other program exited while holding the lock; it is ours now.
            if kernel32.WaitForSingleObject(handle, 250) in (WAIT_OBJECT_0, WAIT_ABANDONED):
                break
            if cancelled() or time.monotonic() >= deadline:
                raise InputBusy("The other Auto-Resume is using the keyboard; will try again")
        try:
            yield
        finally:
            kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


def worker_cancelled(worker):
    """Stop and pending UI commands must not wait behind the other program."""
    def cancelled():
        stop = getattr(worker, "_stop_event", None)
        cmds = getattr(worker, "cmds", None)
        return bool((stop is not None and stop.is_set()) or (cmds is not None and not cmds.empty()))
    return cancelled
