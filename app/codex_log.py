"""Reset time of the latest usage-limit rejection, read from Codex's local session logs.

The ChatGPT desktop app runs its Codex conversations through the Codex engine, which
appends every event to ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (CODEX_HOME
overrides ~/.codex). Two records matter:

  {"timestamp": "...Z", "type": "event_msg", "payload": {"type": "token_count",
   "rate_limits": {"primary": {"used_percent": 100.0, "window_minutes": 300,
   "resets_at": 1789457599}, "secondary": {...}}}}
  {"timestamp": "...Z", "type": "event_msg", "payload": {"type": "task_complete",
   "error": {"message": "You've hit your usage limit. ... or try again at 9:33 AM.",
   "codex_error_info": "usage_limit_exceeded"}}}

Limits belong to the account, so the newest rejection in any log tells when the
used-up window resets, even though the app's own notice may leave the time out.

Read-only: only the tails of recently written logs are scanned, only these two
record types are parsed, and nothing is kept except the parsed times.
"""
import datetime as dt
import json
import os
import time

from chatgpt_limits import notice_reset

TAIL_BYTES = 1024 * 1024    # a rejection is among the last records while the chat is blocked
MAX_AGE_S = 8 * 86400       # a weekly reset can come days after the rejection
MAX_FILES = 200


def default_root():
    base = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(base, "sessions")


def _local(timestamp):
    """ISO 8601 UTC ("...Z") -> naive local datetime."""
    return dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def _spent_reset(rate_limits):
    """Latest reset among the windows that are used up in a rate-limit snapshot."""
    resets = []
    for name in ("primary", "secondary"):
        window = rate_limits.get(name) or {}
        try:
            if float(window.get("used_percent", 0)) >= 100 and window.get("resets_at"):
                resets.append(dt.datetime.fromtimestamp(float(window["resets_at"])))
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return max(resets, default=None)


class CodexLog:
    def __init__(self, root=None):
        self.root = root or default_root()
        self._cache = {}    # path -> ((mtime_ns, size), record or None)

    def latest_reset(self):
        """Naive local datetime when the newest usage-limit rejection clears, or None."""
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

    def context_for(self, title):
        """How much of its context window the conversation with this title has used.

        `session_index.jsonl` next to the sessions folder maps a conversation's name to
        its session id, and the id ends the name of its rollout file. The file's last
        `token_count` record carries the tokens in the context window and the model's
        window size. Returns dict(used, window, pct) or None when any of it is missing.
        """
        session_id = self._session_id(title)
        if not session_id:
            return None
        path = next((p for p, _ in self._recent_logs() if p.endswith(session_id + ".jsonl")), None)
        if path is None:
            return None
        used, window = self._last_token_count(path)
        if not used or not window:
            return None
        return dict(used=used, window=window, pct=round(100 * used / window))

    def _session_id(self, title):
        """Newest session whose thread name is exactly this title, or None."""
        index = os.path.join(os.path.dirname(self.root), "session_index.jsonl")
        best = None
        try:
            with open(index, "rb") as f:
                for raw in f:
                    if b'"thread_name"' not in raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except ValueError:
                        continue
                    if record.get("thread_name") != title or not record.get("id"):
                        continue
                    stamp = str(record.get("updated_at") or "")
                    if best is None or stamp > best[0]:
                        best = (stamp, record["id"])
        except OSError:
            return None
        return best[1] if best else None

    @staticmethod
    def _last_token_count(path):
        """(tokens in the context window, window size) from the file's last token_count."""
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - TAIL_BYTES))
                data = f.read(TAIL_BYTES)
        except OSError:
            return None, None
        lines = data.split(b"\n")
        for raw in reversed(lines):
            if b'"token_count"' not in raw:
                continue
            try:
                info = json.loads(raw)["payload"].get("info")
                usage = info["last_token_usage"]
                return int(usage["total_tokens"]), int(info["model_context_window"])
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
        return None, None

    def _recent_logs(self):
        cutoff = time.time() - MAX_AGE_S
        logs = []
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(folder, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                if stat.st_mtime >= cutoff:
                    logs.append((path, stat))
        logs.sort(key=lambda item: item[1].st_mtime, reverse=True)
        return logs[:MAX_FILES]

    def _scan(self, path, size):
        """The last usage-limit rejection in the file's tail, with its reset, or None."""
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
        snapshot, found = None, None
        for raw in lines:
            if b'"event_msg"' not in raw:
                continue
            is_limits = b'"rate_limits"' in raw
            is_rejection = b'"usage_limit_exceeded"' in raw and b'"task_complete"' in raw
            if not (is_limits or is_rejection):
                continue
            try:
                record = json.loads(raw)
                payload = record["payload"]
                recorded = _local(record["timestamp"])
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if payload.get("type") == "token_count" and isinstance(payload.get("rate_limits"), dict):
                limits = payload["rate_limits"]
                if limits.get("primary") or limits.get("secondary"):
                    snapshot = limits
                continue
            error = payload.get("error") if payload.get("type") == "task_complete" else None
            if not isinstance(error, dict) or error.get("codex_error_info") != "usage_limit_exceeded":
                continue
            # The snapshot sent with the rejected request names the used-up window;
            # the message is the fallback ("try again at 9:33 AM" relative to the record).
            resets_at = (_spent_reset(snapshot) if snapshot else None) or \
                notice_reset(str(error.get("message") or ""), recorded)
            if resets_at:
                found = dict(recorded=recorded, resets_at=resets_at)
        return found
