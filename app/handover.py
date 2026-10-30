# -*- coding: utf-8 -*-
"""Handing a full conversation over to a fresh one.

When a conversation has used up most of its context window, the program interrupts it,
asks the agent to write a handover for its successor, waits for the file and the agreed
marker line, then opens a new conversation in the same project and tells it to carry on
(or to take the next section of the plan).

Nothing here touches a window: the flow asks the app's adapter to stop, send or start a
conversation, so every decision can be tested on plain data.
"""
from dataclasses import dataclass
import datetime as dt
import json
import os
import re

from strings import Text

# ------------------------------------------------------------ reading the context

CONTEXT_RE = re.compile(
    r"(?:context|kontekst)\s*:?\s*(?P<used>\d+(?:[.,]\d+)?)\s*(?P<uu>[kKmM])?"
    r"(?:\s*/\s*(?P<win>\d+(?:[.,]\d+)?)\s*(?P<wu>[kKmM])?)?"
    r"(?:\s*\(\s*(?P<pct>\d{1,3})\s*%\s*\))?", re.I)
CONTEXT_PCT_RE = re.compile(r"(?:context|kontekst)\s*:?\s*(?P<pct>\d{1,3})\s*%", re.I)
UNITS = {"": 1, "k": 1_000, "m": 1_000_000}


def _tokens(number, unit):
    return int(round(float(str(number).replace(",", ".")) * UNITS[(unit or "").lower()]))


def context_label(context):
    """How the context reads in the window: "712k / 1M (71%)", "71%" or "—"."""
    if not context:
        return "—"
    used, window, pct = context.get("used"), context.get("window"), context.get("pct")
    share = "%d%%" % pct if pct is not None else ""
    if used is None:
        return share or "—"
    size = _short(used) + (" / " + _short(window) if window else "")
    return size + (" (%s)" % share if share else "")


def _short(tokens):
    """700000 -> "700k", 1000000 -> "1M", 258400 -> "258.4k"."""
    for unit, size in (("M", 1_000_000), ("k", 1_000)):
        if tokens >= size:
            value = tokens / size
            return ("%.1f" % value).rstrip("0").rstrip(".") + unit
    return str(tokens)


THRESHOLD_RE = re.compile(r"^\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>[kKmM%]?)\s*$")
MIN_TOKENS, MIN_PCT, MAX_PCT = 20_000, 20, 95


def parse_threshold(text):
    """("tokens", 700000) for 700k / 0.7M / 700000, ("pct", 70) for 70%, else None.

    Clamped to a safe range: a threshold a fresh conversation would already be over
    would hand the work on again and again.
    """
    match = THRESHOLD_RE.match(text or "")
    if not match:
        return None
    if match["unit"] == "%":
        percent = round(float(match["num"].replace(",", ".")))
        return "pct", int(min(MAX_PCT, max(MIN_PCT, percent)))
    return "tokens", max(MIN_TOKENS, _tokens(match["num"], match["unit"]))


def over_threshold(context, threshold):
    """True when the reading has reached the threshold.

    A reading that cannot be compared — no token count for a threshold in tokens, no
    percentage for one in percent — is never over it.
    """
    if not context or not threshold:
        return False
    kind, value = threshold
    if kind == "tokens":
        return bool(context.get("used")) and context["used"] >= value
    percent = context.get("pct")
    if percent is None and context.get("used") and context.get("window"):
        percent = 100 * context["used"] / context["window"]
    return percent is not None and percent >= value


def context_from_meter(name):
    """dict(used, window, pct) read from the bottom-bar meter's name, or None.

    "Usage: Context 195.8k / 1M (20%), 45% of 5-hour limit" -> 195800 of 1000000, 20%.
    A fresh session says "Context 0" with no window, and an older build of the app only
    says "context 20%": then the missing numbers stay None.
    """
    name = name or ""
    pct_only = CONTEXT_PCT_RE.search(name)
    if pct_only:
        return dict(used=None, window=None, pct=int(pct_only["pct"]))
    match = CONTEXT_RE.search(name)
    if not match:
        return None
    used = _tokens(match["used"], match["uu"])
    window = _tokens(match["win"], match["wu"]) if match["win"] else None
    if match["pct"]:
        pct = int(match["pct"])
    else:
        pct = round(100 * used / window) if window else None
    return dict(used=used, window=window, pct=pct)


# ---------------------------------------------- the handover file and its marker

HANDOVER_DIR = ".handover"
HANDOVER_REL = HANDOVER_DIR + "/HANDOVER-{id}.md"
SENTINEL_RE = re.compile(r"HANDOVER\s+READY\s+(?P<id>\d{8}-\d{6})\s*:\s*(?P<path>\S[^\n]*?\.md)", re.I)
STRIP = "`\"'<>*() \t"
FILE_SLACK = dt.timedelta(minutes=1)    # the file may be saved a moment before the reply


def new_id(now=None):
    return (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")


def sentinel_path(text, handover_id):
    """The path from the agreed marker line, or None. Only our own handover counts."""
    for match in SENTINEL_RE.finditer(text or ""):
        if match["id"] == handover_id:
            return match["path"].strip(STRIP).strip()
    return None


def sentinel_any(text):
    """Does the text carry any handover marker? A conversation that ends with one has
    already been handed over, even if this run of the program never saw it happen."""
    return bool(SENTINEL_RE.search(text or ""))


def file_ok(path, handover_id, since):
    """The handover exists, is not empty, sits where we asked and was written for us."""
    wanted = HANDOVER_REL.format(id=handover_id).replace("/", os.sep)
    if not path or os.path.normpath(path).casefold()[-len(wanted):] != wanted.casefold():
        return False
    try:
        stat = os.stat(path)
    except OSError:
        return False
    written = dt.datetime.fromtimestamp(stat.st_mtime)
    return stat.st_size > 0 and written >= since - FILE_SLACK


def project_root(path):
    """The folder that holds .handover — the project the agent is working in."""
    folder = os.path.dirname(path or "")
    if os.path.basename(folder).casefold() != HANDOVER_DIR:
        return None
    return os.path.dirname(folder) or None


def git_branch(folder):
    """The branch checked out in `folder`, read from git's own files (a worktree's ".git"
    is a file naming its real directory); None outside a repository or on a detached
    HEAD. Nothing is run, so no console window flashes."""
    path = os.path.abspath(folder or ".")
    while True:
        dot = os.path.join(path, ".git")
        if os.path.isdir(dot):
            head = os.path.join(dot, "HEAD")
            break
        if os.path.isfile(dot):
            try:
                with open(dot, encoding="utf-8") as f:
                    line = f.read().strip()
            except OSError:
                return None
            if not line.startswith("gitdir:"):
                return None
            gitdir = line[len("gitdir:"):].strip()
            head = os.path.join(gitdir if os.path.isabs(gitdir) else os.path.join(path, gitdir), "HEAD")
            break
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    try:
        with open(head, encoding="utf-8") as f:
            ref = f.read().strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


# ------------------------------------------------------------- the two messages

def _one_line(*parts):
    """Both messages are sent as one message: Enter submits, so no line breaks."""
    return " ".join(" ".join(part for part in parts if part).split())


def request_message(cfg, t, context_label_text, handover_id):
    """What the full conversation is asked to do. The body can be edited in the
    settings; the tail with the file name and the marker is always ours."""
    body = (cfg.get("handover_request_text") or "").strip() or t(
        "handover_request_default", context=context_label_text)
    return _one_line(body, t("handover_request_tail",
                             path=HANDOVER_REL.format(id=handover_id), id=handover_id))


def continue_message(cfg, t, path, plan_folder, folder=None, branch=None, wrapup=False):
    """What the fresh conversation is told. The body can be edited in the settings; the
    tail with the handover's path, where the work goes on and the plan folder is always
    ours. After a wrap-up the body is built in: a wrap-up's summary is shorter than a
    handover, so the new agent gets to know the project before it reads it."""
    if wrapup:
        body = t("handover_continue_wrapup")
    else:
        body = (cfg.get("handover_continue_text") or "").strip() or t("handover_continue_default")
    plan = t("handover_continue_plan", plan=plan_folder) if plan_folder else t("handover_continue_noplan")
    where = (t("handover_continue_where", folder=folder, branch=branch) if folder and branch else
             t("handover_continue_folder", folder=folder) if folder else "")
    return _one_line(body, t("handover_continue_tail", path=path), where, plan)


WRAPUP_MARK = "<!-- auto-resume wrap-up "


def wrapup_mark(title, info):
    """The file's first line: which wrap-up of which conversation it was made from, so the
    same wrap-up is never handed over twice, not even by a later run of the program."""
    return WRAPUP_MARK + json.dumps({"chat": title, "at": info["at"].isoformat()}, ensure_ascii=False) + " -->"


def handed_over_before(folder, title, info):
    """The handover file this very wrap-up was already turned into, or None."""
    mark = wrapup_mark(title, info)
    folder = os.path.join(folder, HANDOVER_DIR)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in names:
        if not (name.startswith("HANDOVER-") and name.endswith(".md")):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding="utf-8") as f:
                if f.readline().rstrip("\n") == mark:
                    return path
        except (OSError, UnicodeDecodeError):
            continue
    return None


def wrapup_file_text(t, title, info, reset):
    """The handover a wrap-up leaves behind: where the work was, then the previous agent's
    own summary, word for word. `info` is QuotaLog.wrap_up_for's dict."""
    lines = [
        wrapup_mark(title, info),
        t("handover_wrapup_file_title"), "",
        "- " + t("handover_wrapup_file_chat", chat=title),
        "- " + t("handover_wrapup_file_folder", folder=info["folder"]),
        "- " + t("handover_wrapup_file_branch", branch=info["branch"] or "—"),
        "- " + t("handover_wrapup_file_at", at=f"{info['at']:%Y-%m-%d %H:%M}"),
        "- " + t("handover_wrapup_file_reset", reset=f"{reset:%Y-%m-%d %H:%M}" if reset else "—"),
        "", t("handover_wrapup_file_note"), "", "---", "",
        info["summary"].strip(), ""]
    return "\n".join(lines)


def write_wrapup_file(folder, handover_id, text):
    """Save the handover where a handover is expected and return its path. It is written
    aside and then moved into place, so no half-written file is ever read, and nothing is
    left behind when either step fails. The text goes in as it is, line breaks too."""
    path = os.path.join(folder, HANDOVER_REL.format(id=handover_id).replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    aside = path + ".part"
    try:
        with open(aside, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(aside, path)
    except Exception:
        try:
            os.remove(aside)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------- the flow

STOP_WAIT_S = 60            # between attempts to stop a conversation that keeps working
MAX_STOPS = 3
MAX_REMINDERS = 1
WORKING_PHASES = ("stopping", "requested", "reminded", "ready")
# phases the scheduler owns: the handover only waits then, and its clock stops
BLOCKED_PHASES = ("waiting", "verifying", "question", "exhausted")
LIMIT_WINDOW = dt.timedelta(hours=5)    # a wrap-up belongs to the five-hour window it ended


class HandoverRefused(RuntimeError):
    """The window could not confirm what the handover needs; do not try again by itself."""


@dataclass
class HandoverState:
    id: str = ""
    phase: str = "idle"        # idle, stopping, requested, reminded, ready, handed, attention
    pending: str = ""          # an id handed to a limit resume, before it really went out
    stops: int = 0
    reminders: int = 0
    work_s: float = 0.0        # time spent on this handover, blocked scans aside
    path: str = ""
    started: object = None     # when the request went out (the file must be newer)
    next_try: object = None    # earliest next Stop
    new_key: str = ""
    context: str = ""
    last_seen: object = None
    wrapup: bool = False       # built from a wrap-up's own summary: the new agent is told so
    missing: str = ""          # why the wrap-up could not be used, as last logged


class HandoverFlow:
    """Per-conversation state machine: stop, ask, wait, open a new conversation.

    Every side effect goes through the app's adapter (`stop`, `send`, `new_chat`), and a
    phase only moves on once the adapter says the step really happened.
    """

    def __init__(self, engine):
        self.engine = engine
        self.states = {}

    # ------------------------------------------------------------- plumbing
    @property
    def cfg(self):
        return self.engine.worker.cfg

    def t(self, key, **kw):
        return self.engine.worker.t(key, **kw)

    def state_for(self, key):
        return self.states.setdefault(key, HandoverState())

    def active_key(self):
        return next((key for key, state in self.states.items() if state.phase in WORKING_PHASES), None)

    def clear(self):
        self.states.clear()

    def threshold(self):
        return parse_threshold(self.cfg.get("handover_threshold"))

    def enabled(self):
        return bool(self.cfg.get("handover_enabled") and self.cfg.get("auto_send"))

    def due(self, key, observed):
        """Is it time to hand this conversation over?"""
        if not self.enabled() or self.state_for(key).phase != "idle":
            return False
        if not over_threshold(observed.get("context"), self.threshold()):
            return False
        if observed.get("question") or observed.get("permission") or observed.get("limit"):
            return False
        if sentinel_any(observed.get("last_text")):
            return False          # this conversation has already been handed over
        return self.active_key() in (None, key)

    # ------------------------------------------------ the limit path (resume)
    def resume_text(self, key, session, observed, now):
        """What a limit resume should send instead of the user's message, or None.

        The phase does not move here: only `resume_sent` knows whether the message
        really went out.
        """
        state = self.state_for(key)
        if state.phase in ("requested", "reminded"):
            return self.t("handover_request_again", id=state.id)
        if not self.due(key, dict(observed, limit=False)):
            return None
        state.pending = new_id(now)
        state.context = context_label(observed.get("context"))
        return request_message(self.cfg, self.t, state.context, state.pending)

    def resume_sent(self, key, now):
        """The limit resume went out: from now on we are waiting for the handover."""
        state = self.state_for(key)
        if not state.pending:
            return
        state.id, state.pending = state.pending, ""
        self._requested(key, state, now)

    # ------------------------------------------- the limit path, after a wrap-up
    def from_wrap_up(self, key, session, observed, now):
        """After a wrap-up with the context over the threshold, hand the work over from the
        summary Claude wrote when the limit ran out (the conversation's log), without a word
        to the old conversation: after the five-hour wait its prompt cache is gone, and one
        more message would read the whole context again.

        True when this took the conversation over: a new one is open or the flow needs
        attention (an error of the adapter propagates; the flow retries from "ready" on
        the next scan). False when nothing was done and the usual request should go out.
        """
        if not self.due(key, dict(observed, limit=False)):
            return False
        info = self._wrap_up_info(key, session)
        if isinstance(info, str):
            self._no_wrap_up(key, info)
            return False
        earlier = handed_over_before(info["folder"], chat_name(key), info)
        if earlier:
            # This wrap-up was handed over before (by an earlier run, or before Stop): never
            # twice, and the old conversation is not resumed either.
            state = self.state_for(key)
            state.path, state.wrapup, state.phase = earlier, True, "handed"
            session.phase = "handed"
            self.engine.note(key, session, "handed")
            self.engine.worker.log("log_handover_wrapup_done", chat=chat_name(key), path=earlier)
            return True
        handover_id = new_id(now)
        try:
            path = write_wrapup_file(info["folder"], handover_id,
                                     wrapup_file_text(self.t, chat_name(key), info, session.reset))
        except Exception:          # not only the disk: a summary may not encode either
            self._no_wrap_up(key, "wrapup_not_saved")
            return False
        state = self.state_for(key)
        state.context = context_label(observed.get("context"))
        self.engine.worker.log("log_handover_from_wrapup", chat=chat_name(key), context=state.context)
        self.engine.worker.log("log_handover_file", chat=chat_name(key), path=path)
        state.id, state.path, state.wrapup, state.phase = handover_id, path, True, "ready"
        state.started = state.last_seen = now
        state.work_s = 0.0
        session.phase = "handover_new"      # the scheduler leaves this conversation alone now
        self._open_new(key, session, state, now)
        return True

    def _wrap_up_info(self, key, session):
        """This limit's wrap-up from the conversation's log, or why there is none (a key)."""
        try:
            info = self.engine.quota.wrap_up_for(chat_name(key))
        except Exception:          # an unreadable log must never stop the watch
            info = None
        if not info:
            return "wrapup_no_summary"
        if session.reset:
            # A reset more than five hours after the limit was first seen is not the five-hour
            # limit's (a weekly one): its wrap-up counts from that first sighting.
            longer = session.since is not None and session.reset - session.since > LIMIT_WINDOW
            current = info["at"] <= session.reset and (
                session.reset - LIMIT_WINDOW < info["at"]
                or longer and session.since - LIMIT_WINDOW < info["at"])
        else:
            # Unknown reset: the wait can last the whole window, so count from the first sighting.
            current = session.since is not None and info["at"] > session.since - LIMIT_WINDOW
        if not current:
            return "wrapup_too_old"
        if not info["folder"] or not os.path.isdir(info["folder"]):
            return "wrapup_no_folder"
        return info

    def holds(self, key, session):
        """True when a handover owns this conversation past asking for one: it is opening the
        new conversation, has handed the work over, or waits for a person. The scheduler then
        sends it nothing, and its status follows the handover's."""
        phase = self.state_for(key).phase
        if phase not in ("ready", "handed", "attention"):
            return False
        session.phase = {"ready": "handover_new", "handed": "handed", "attention": "exhausted"}[phase]
        return True

    def _no_wrap_up(self, key, why):
        """Log why the wrap-up could not be used, once: a resume tried again finds the same."""
        state = self.state_for(key)
        if state.missing == why:
            return
        state.missing = why
        self.engine.worker.log("log_handover_wrapup_missing", "warn", chat=chat_name(key),
                               why=self.engine.worker.text(why))

    # ------------------------------------------------------------- the steps
    def step(self, key, session, observed, now, pane, blocked):
        """Called once per scan of this conversation, with the input lock held."""
        state = self.state_for(key)
        if state.phase in ("handed", "attention"):
            return
        if state.phase == "idle":
            if not blocked and self.due(key, observed):
                self._start(key, session, observed, now, pane)
            return
        self._clock(state, now, blocked)
        if blocked:
            return                # the limit or error logic owns the conversation now
        if state.work_s > 60 * self.cfg.get("handover_timeout_min", 30):
            self._attention(key, session, state, "handover_timeout")
            return
        if state.phase == "stopping":
            self._stopping(key, session, state, observed, now, pane)
        elif state.phase in ("requested", "reminded"):
            self._waiting(key, session, state, observed, now, pane)
        if state.phase == "ready":
            if state.wrapup and observed.get("busy"):
                # The old conversation works again (its draft was sent, or someone resumed it):
                # a second agent beside it is the person's call, not ours.
                self._attention(key, session, state, "handover_wrapup_busy")
                return
            self._open_new(key, session, state, now)

    def _clock(self, state, now, blocked):
        """Time spent on this handover. A conversation waiting for a limit reset is not
        making us wait, so those scans do not count."""
        if state.last_seen is not None and not blocked:
            step = (now - state.last_seen).total_seconds()
            state.work_s += max(0.0, min(step, 4 * self.cfg.get("scan_interval_s", 20)))
        state.last_seen = now

    def _start(self, key, session, observed, now, pane):
        state = self.state_for(key)
        state.context = context_label(observed.get("context"))
        state.last_seen, state.work_s = now, 0.0
        self.engine.worker.log("log_handover_start", chat=chat_name(key), context=state.context)
        if observed.get("busy"):
            self._stop(key, session, state, now, pane)
            return
        state.id = new_id(now)
        self.engine.ui.send(pane, request_message(self.cfg, self.t, state.context, state.id))
        self._requested(key, state, now)
        self.engine.note(key, session, "handover_wait")
        session.phase = "handover_wait"

    def _requested(self, key, state, now):
        state.phase, state.started, state.reminders = "requested", now, 0

    def _stop(self, key, session, state, now, pane):
        """Interrupt the work, so the agent can write the handover instead."""
        state.phase, state.next_try = "stopping", now + dt.timedelta(seconds=STOP_WAIT_S)
        state.stops += 1
        session.phase = "handover_stop"
        self.engine.ui.stop(pane)
        self.engine.note(key, session, "handover_stop")

    def _stopping(self, key, session, state, observed, now, pane):
        if not observed.get("busy"):
            state.id = new_id(now)
            self.engine.ui.send(pane, request_message(self.cfg, self.t, state.context, state.id))
            self._requested(key, state, now)
            session.phase = "handover_wait"
            self.engine.note(key, session, "handover_wait")
            return
        if state.next_try and now < state.next_try:
            return
        if state.stops >= MAX_STOPS:
            self._attention(key, session, state, "handover_stop_failed")
            return
        self._stop(key, session, state, now, pane)

    def _waiting(self, key, session, state, observed, now, pane):
        """Wait for the agreed marker line and the file it names."""
        if observed.get("busy"):
            return
        text = observed.get("last_text") or ""
        path = sentinel_path(text, state.id)
        if path and file_ok(path, state.id, state.started):
            state.path, state.phase = path, "ready"
            self.engine.worker.log("log_handover_file", chat=chat_name(key), path=path)
            return
        if state.reminders >= MAX_REMINDERS:
            self._attention(key, session, state,
                            "handover_no_file" if path else "handover_no_marker")
            return
        state.reminders += 1
        state.phase = "reminded"
        self.engine.ui.send(pane, self.t("handover_request_again", id=state.id))
        self.engine.note(key, session, "handover_reminded")

    def _open_new(self, key, session, state, now):
        """Start a fresh conversation in the same project and hand the work to it."""
        root = project_root(state.path)
        if not root:
            self._attention(key, session, state, "handover_no_project")
            return
        session.phase = "handover_new"
        message = continue_message(self.cfg, self.t, state.path, self.cfg.get("plan_folder"),
                                   root, git_branch(root), wrapup=state.wrapup)
        try:
            new_key = self.engine.ui.new_chat(key, root, message)
        except HandoverRefused as refused:
            self._attention(key, session, state, str(refused))
            return
        state.new_key, state.phase = new_key or "", "handed"
        session.phase = "handed"
        self._follow(key, new_key, now)
        self.engine.note(key, session, "handed")
        self.engine.worker.log("log_handover_new_chat", chat=chat_name(key),
                               title=chat_name(new_key) if new_key else "—")
        self._log_settings(key, getattr(self.engine.ui, "last_settings", None))

    def _log_settings(self, key, settings):
        """Where and how the new conversation works, and what it could not be given."""
        if not settings:
            return
        shown = [os.path.basename(os.path.normpath(settings.get("folder") or "")), settings.get("branch"),
                 settings.get("model"), settings.get("effort"), settings.get("mode")]
        self.engine.worker.log("log_handover_settings", chat=chat_name(key),
                               settings=" · ".join(part for part in shown if part))
        if settings.get("missed"):
            self.engine.worker.log("log_handover_missed", "warn", chat=chat_name(key),
                                   missed=Text.joined(", ", [self.engine.worker.text("setting_" + part)
                                                             for part in settings["missed"]]))

    def _follow(self, key, new_key, now):
        """Watch the new conversation instead of the old one, and check that it started."""
        if not new_key:
            return
        from session_automation import SessionState
        self.states.setdefault(new_key, HandoverState())
        self.engine.sessions[new_key] = SessionState(
            phase="verifying", due=now + dt.timedelta(seconds=self.cfg.get("verify_delay_s", 30)))
        if self.cfg.get("watch_scope") == "selected":
            chats = list(self.cfg.get("selected_chats", []))
            if key in chats and new_key not in chats:
                chats[chats.index(key)] = new_key
                self.cfg["selected_chats"] = chats
                # The window's checks are what it sends back with every settings change:
                # they have to follow too, or the next change would watch the old one again.
                self.engine.worker.emit("selection", chats)

    def _attention(self, key, session, state, note):
        """Nothing more is sent by itself: the person has to look."""
        state.phase = "attention"
        session.phase = "exhausted"
        self.engine.worker.emit("beep")
        self.engine.note(key, session, note)


def chat_name(key):
    return (key or "").split(":", 1)[-1]
