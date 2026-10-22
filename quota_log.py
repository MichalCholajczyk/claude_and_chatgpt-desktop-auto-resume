"""Reset time of the latest usage-limit rejection, read from Claude Code's local logs.

Claude Code appends each rejected request to ~/.claude/projects/<project>/<session>.jsonl:
{"error": "rate_limit", "timestamp": "...Z", "quotaLimits": {"status": "rejected",
"resetsAt": <epoch seconds>, "rateLimitType": "five_hour"}}. Limits belong to the
account, so the newest rejection in any log carries the reset time even when a
conversation's own notice leaves it out ("Try again after your session limit resets.").

Read-only: only the tails of recently written logs are scanned, only rate-limit records
are parsed, and nothing is kept except the parsed reset time.
"""
import datetime as dt
import json
import os
import re
import time

TAIL_BYTES = 512 * 1024     # a rejection is among the last records while the chat is blocked
MAX_AGE_S = 8 * 86400       # a weekly reset can come days after the rejection
MAX_FILES = 300
WRAPUP_TAIL_BYTES = 2 * 1024 * 1024   # the wrap-up ends the log: the conversation stood still since


def default_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def local_time(timestamp):
    """ISO 8601 UTC ("...Z") -> naive local datetime, like the program's other clocks."""
    return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def project_folder(cwd, log_dir):
    """The folder the conversation works in: `cwd` or the ancestor of it that Claude Code
    named the log's directory after (every character but a letter or a digit becomes "-").
    The agent's shell may have moved deeper since (seen 15.10). "" when none matches."""
    path = os.path.normpath(cwd) if cwd else ""
    while path:
        if re.sub(r"[^A-Za-z0-9]", "-", path).casefold() == log_dir.casefold():
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent
    return ""


def tail_lines(path, size, limit):
    """The whole records in the file's last `limit` bytes; [] when it cannot be read."""
    start = max(0, size - limit)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(limit)
    except OSError:
        return []
    lines = data.split(b"\n")
    return lines[1:] if start else lines          # the first line is cut mid-record


def typed(record):
    """True for a message someone typed: a user record with text of its own (a string, or
    text blocks). Not a hidden note of Claude Code's ("isMeta"), nor a subagent's prompt;
    a tool's result keeps its output inside its own block."""
    if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return any(isinstance(block, dict) and block.get("type") == "text" and (block.get("text") or "").strip()
               for block in content or ())


class QuotaLog:
    def __init__(self, root=None):
        self.root = root or default_root()
        self._cache = {}    # path -> ((mtime_ns, size), record or None)
        self._tails = {}    # path -> ((mtime_ns, size), dict(title, used)), recent logs only

    def context_for(self, title):
        """Tokens in the context window of the conversation with this title, or None.

        Claude 2.37 no longer shows them on its meter, but the conversation's log has
        them: it repeats the title ({"type": "custom-title", "customTitle": …}) as the
        conversation goes, and every reply records what its request read — the new input
        plus what came from and went into the prompt cache, which together are the
        context. The newest log whose latest title is this one counts; a subagent's
        replies ("isSidechain") have contexts of their own and are skipped.
        """
        found = None
        for path, stat in self._logs():                 # newest first
            tail = self._tail(path, stat)
            if found is None and tail["title"] == title and tail["used"] is not None:
                found = tail
        return dict(used=found["used"], window=None, pct=None) if found else None

    def _logs(self):
        """The recent logs, newest first. What was read from the others is forgotten: the
        program runs for weeks."""
        logs = self._recent_logs()
        recent = {path for path, _ in logs}
        self._tails = {path: tail for path, tail in self._tails.items() if path in recent}
        return logs

    def _tail(self, path, stat):
        """dict(title, used) of the file's tail, read once per version of the file: the
        latest title and what the latest reply read, each None when the tail has none."""
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._tails.get(path)
        if cached is None or cached[0] != key:
            cached = self._tails[path] = (key, self._scan_tail(path, stat.st_size))
        return cached[1]

    @staticmethod
    def _scan_tail(path, size):
        title = used = None
        for raw in reversed(tail_lines(path, size, TAIL_BYTES)):
            if title is None and b'"custom-title"' in raw:
                try:
                    title = json.loads(raw).get("customTitle")
                except ValueError:
                    pass
            elif used is None and b'"usage"' in raw and b'"assistant"' in raw:
                try:
                    record = json.loads(raw)
                    if record.get("type") == "assistant" and not record.get("isSidechain"):
                        usage = record["message"]["usage"]
                        used = sum(int(usage.get(k) or 0) for k in (
                            "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
            if title is not None and used is not None:
                break
        return dict(title=title, used=used)

    def wrap_up_for(self, title):
        """The wrap-up of the conversation with this title, from its log: dict(summary,
        folder, branch, at), or None.

        When the limit runs out mid-response, Claude Code logs a hidden note
        ({"usageLimitNote": "wrap_up", "cwd", "gitBranch", "timestamp"}); the first reply
        that ends the turn after it is Claude's summary of what was done and what was left
        (seen 15.10). Not one after a message the person typed: that answers the message.
        As for the context, the newest log whose latest title is this one counts.
        """
        for path, stat in self._logs():                 # newest first
            if self._tail(path, stat)["title"] == title:
                return self._scan_wrap_up(path, stat.st_size)
        return None

    @staticmethod
    def _scan_wrap_up(path, size):
        """dict(summary, folder, branch, at) of the last wrap-up in the file's tail, or None."""
        lines = tail_lines(path, size, WRAPUP_TAIL_BYTES)
        note = start = None
        for i in range(len(lines) - 1, -1, -1):
            if b'"usageLimitNote"' not in lines[i]:
                continue
            try:
                record = json.loads(lines[i])
            except ValueError:
                continue
            if record.get("usageLimitNote") == "wrap_up":
                note, start = record, i + 1
                break
        if note is None:
            return None
        replies = []                                    # (message id, stop reason, texts)
        for raw in lines[start:]:
            if b'"assistant"' not in raw and b'"user"' not in raw:
                continue
            try:
                record = json.loads(raw)
                if typed(record):
                    break               # the wrap-up's turn is over: what follows answers the person
                if record.get("type") != "assistant" or record.get("isSidechain"):
                    continue
                message = record["message"]
                texts = [block["text"].strip() for block in message.get("content") or ()
                         if isinstance(block, dict) and block.get("type") == "text"
                         and (block.get("text") or "").strip()]
                replies.append((message.get("id"), message.get("stop_reason"), texts))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        closing = next((reply for reply in replies if reply[1] == "end_turn"), None)
        if closing is None:
            return None
        # The blocks of one message are logged one by one, under one id.
        parts = [closing] if closing[0] is None else [reply for reply in replies if reply[0] == closing[0]]
        summary = "\n\n".join(text for _, _, texts in parts for text in texts)
        try:
            at = local_time(note["timestamp"])
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
        if not summary:
            return None
        folder = project_folder(note.get("cwd"), os.path.basename(os.path.dirname(path)))
        return dict(summary=summary, folder=folder, branch=note.get("gitBranch") or "", at=at)

    def latest_reset(self):
        """Naive local datetime when the newest rejected request's limit resets, or None."""
        newest = None
        cache = {}
        for path, stat in self._recent_logs():
            key = (stat.st_mtime_ns, stat.st_size)
            cached = self._cache.get(path)
            if cached is None or cached[0] != key:
                cached = (key, self._scan(path, stat.st_size))
            cache[path] = cached
            record = cached[1]
            if record and (newest is None or record["recorded"] > newest["recorded"]):
                newest = record
        self._cache = cache
        return newest["resets_at"] if newest else None

    def _recent_logs(self):
        cutoff = time.time() - MAX_AGE_S
        logs = []
        try:
            projects = [entry.path for entry in os.scandir(self.root) if entry.is_dir()]
        except OSError:
            return []
        for project in projects:
            try:
                for entry in os.scandir(project):
                    if entry.is_file() and entry.name.endswith(".jsonl"):
                        stat = entry.stat()
                        if stat.st_mtime >= cutoff:
                            logs.append((entry.path, stat))
            except OSError:
                continue
        logs.sort(key=lambda item: item[1].st_mtime, reverse=True)
        return logs[:MAX_FILES]

    def _scan(self, path, size):
        """The last rejected rate-limit record in the file's tail, or None."""
        start = max(0, size - TAIL_BYTES)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(TAIL_BYTES)
        except OSError:
            return None
        lines = data.split(b"\n")
        if start:
            lines = lines[1:]   # the first line is cut mid-record
        for raw in reversed(lines):
            if b'"quotaLimits"' not in raw or b'"rate_limit"' not in raw:
                continue
            try:
                record = json.loads(raw)
                quota = record["quotaLimits"]
                if record.get("error") != "rate_limit" or quota.get("status") != "rejected":
                    continue
                resets_at = float(quota["resetsAt"])
                recorded = dt.datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            return dict(resets_at=dt.datetime.fromtimestamp(resets_at), recorded=recorded,
                        kind=quota.get("rateLimitType"))
        return None
