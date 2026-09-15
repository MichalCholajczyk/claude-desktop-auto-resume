"""Tests for reading the latest usage-limit reset from Claude Code's local logs."""
import datetime as dt
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import quota_log
from quota_log import QuotaLog

RESET_0930 = 1789457400   # 2026-09-15 07:30 UTC, as written by Claude Code on 15.09


def rejection(timestamp, resets_at, status="rejected", error="rate_limit"):
    return json.dumps({"type": "assistant", "timestamp": timestamp, "error": error,
                       "message": {"content": [{"type": "text", "text": "You've hit your session limit"}]},
                       "quotaLimits": {"status": status, "resetsAt": resets_at,
                                       "rateLimitType": "five_hour"}})


class QuotaLogTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def write(self, project, name, *lines):
        folder = os.path.join(self.root, project)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def test_newest_rejection_across_logs_wins(self):
        self.write("flow-chart", "older.jsonl",
                   rejection("2026-09-15T07:25:41.669Z", RESET_0930),
                   json.dumps({"type": "user", "timestamp": "2026-09-15T07:26:00Z"}))
        self.write("fitness", "newer.jsonl", rejection("2026-09-15T12:40:00.000Z", RESET_0930 + 5 * 3600))
        self.write("fitness", "earlier.jsonl", rejection("2026-09-15T05:05:19.913Z", RESET_0930))
        self.assertEqual(QuotaLog(self.root).latest_reset(),
                         dt.datetime.fromtimestamp(RESET_0930 + 5 * 3600))

    def test_only_rejected_rate_limit_records_count(self):
        self.write("p", "s.jsonl",
                   rejection("2026-09-15T05:05:19Z", RESET_0930),
                   "not json at all",
                   rejection("2026-09-15T06:00:00Z", RESET_0930 + 3600, status="allowed_warning"),
                   rejection("2026-09-15T06:30:00Z", RESET_0930 + 7200, error="overloaded"),
                   '{"type": "assistant", "quotaLimits": {"status": "rejected", "resetsAt": ')
        self.assertEqual(QuotaLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0930))

    def test_record_at_the_end_of_a_large_log_is_found(self):
        padding = json.dumps({"type": "user", "timestamp": "2026-09-15T05:00:00Z", "blob": "x" * 4096})
        self.write("p", "s.jsonl", *([padding] * 40), rejection("2026-09-15T07:25:41Z", RESET_0930))
        with patch.object(quota_log, "TAIL_BYTES", 8192):
            self.assertEqual(QuotaLog(self.root).latest_reset(), dt.datetime.fromtimestamp(RESET_0930))

    def test_unchanged_logs_are_not_read_again(self):
        path = self.write("p", "s.jsonl", rejection("2026-09-15T07:25:41Z", RESET_0930))
        log = QuotaLog(self.root)
        with patch.object(QuotaLog, "_scan", autospec=True, side_effect=QuotaLog._scan) as scan:
            log.latest_reset()
            log.latest_reset()
            self.assertEqual(scan.call_count, 1)
            with open(path, "a", encoding="utf-8") as f:
                f.write(rejection("2026-09-15T12:40:00Z", RESET_0930 + 5 * 3600) + "\n")
            self.assertEqual(log.latest_reset(), dt.datetime.fromtimestamp(RESET_0930 + 5 * 3600))
            self.assertEqual(scan.call_count, 2)

    def test_stale_logs_and_missing_folder_give_nothing(self):
        path = self.write("p", "s.jsonl", rejection("2026-08-01T07:25:41Z", RESET_0930))
        old = time.time() - quota_log.MAX_AGE_S - 3600
        os.utime(path, (old, old))
        self.assertIsNone(QuotaLog(self.root).latest_reset())
        self.assertIsNone(QuotaLog(os.path.join(self.root, "missing")).latest_reset())


if __name__ == "__main__":
    unittest.main()
