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
import time

TAIL_BYTES = 512 * 1024     # a rejection is among the last records while the chat is blocked
MAX_AGE_S = 8 * 86400       # a weekly reset can come days after the rejection
MAX_FILES = 300


def default_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


class QuotaLog:
    def __init__(self, root=None):
        self.root = root or default_root()
        self._cache = {}    # path -> ((mtime_ns, size), record or None)

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
