# -*- coding: utf-8 -*-
"""Tests for the auto-send path — the bug where the app went silent at the one
moment it was supposed to act.

From auto_continue.log, the night of 2026-07-16:

    [2026-07-15 21:20:46] 5-HOUR LIMIT HIT (100%). Resets Thu 00:19. I'll send at 00:20:45.
    [2026-07-16 00:20:51] 5-hour limit is clear (0%) — nothing to send, back to watching.

_do_send() re-read the usage panel before typing and bailed out when the
5-hour row was under the threshold. But the app arms for reset + 60 s, so at
that exact moment the row always reads 0% and the notice is gone — the check
fired on the very state it had spent all night waiting for. Automatic sending
could only ever work when the panel read happened to fail; a manual send was
fine because it skipped the check.

Run:  py test_send_after_reset.py
"""
import datetime as dt
import os
import queue
import tempfile
import types

import claude_auto_continue as m
from claude_auto_continue import MonitorWorker

FAILURES = []


def check(label, cond):
    if cond:
        print(f"  ok  {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL  {label}")


class FakePrompt:
    """Stands in for the chat composer control."""

    def Click(self, simulateMove=False):
        pass


FAKE_WIN = types.SimpleNamespace(
    BoundingRectangle=types.SimpleNamespace(left=0, top=0, right=800, bottom=600))

CLEAR = {"5h": {"pct": 0, "reset": None}}     # what a fresh reset looks like
HIT = {"5h": {"pct": 100, "reset": None}}     # reset hasn't landed yet
BANNER = ["Usage limit reached", "Resets at 2:40 PM"]

MSG = "kontynuuj pracę"


def run_send(rows, ok, texts=(), manual=False):
    """Drive the real _do_send() with the window/UIA layer faked out.
    Returns (worker, keys_typed)."""
    m.LOG_PATH = os.path.join(tempfile.gettempdir(), "auto_resume_selftest.log")

    cfg = dict(m.DEFAULT_CONFIG)
    cfg["message"] = MSG
    cfg["keep_awake"] = False
    w = MonitorWorker(queue.Queue(), cfg)
    w.hwnd = 4242
    w.state = MonitorWorker.ARMED
    w.reset_at = w.send_at = dt.datetime.now()

    w._get_window = lambda: FAKE_WIN
    w._collect = lambda win, budget_s=30.0: (list(texts), FakePrompt(), "session")
    w._read_usage_panel = lambda win: (rows, ok)
    w._focus_window = lambda hwnd: None

    typed = []
    m.auto = types.SimpleNamespace(SendKeys=lambda s, **kw: typed.append(s),
                                   Click=lambda x, y: None)
    m.user32 = types.SimpleNamespace(GetForegroundWindow=lambda: w.hwnd)
    w._do_send(manual=manual)
    return w, typed


def sent_ok(typed):
    """Message typed first, Enter pressed after it."""
    return typed[:1] == [MSG] and "{Enter}" in typed[1:]


# 1. THE BUG. The reset has just happened: the 5-hour row reads 0% and the
#    notice is gone. That is the go-signal — the message must be typed.
w, typed = run_send(CLEAR, True)
check("clear panel at send time -> message typed", sent_ok(typed))
check("clear panel at send time -> waits to verify",
      w.state == MonitorWorker.VERIFY)

# 2. Reset slipped and the limit is still up. Send anyway — _verify_after_send()
#    picks up the new reset time and reschedules.
w, typed = run_send(HIT, True)
check("limit still hit -> message typed anyway", sent_ok(typed))

# 3. Panel unreadable. This is the only case that used to work; keep it working.
w, typed = run_send({}, False)
check("unreadable panel -> message typed", sent_ok(typed))

# 4. The notice is still on screen.
w, typed = run_send(CLEAR, True, texts=BANNER)
check("limit notice still up -> message typed", sent_ok(typed))

# 5. Manual “Send continue now” — worked before, must keep working.
w, typed = run_send(CLEAR, True, manual=True)
check("manual send -> message typed", sent_ok(typed))

# 6. No Claude window -> nothing typed, retry scheduled instead.
cfg = dict(m.DEFAULT_CONFIG)
w = MonitorWorker(queue.Queue(), cfg)
w.state = MonitorWorker.ARMED
w._get_window = lambda: None
typed = []
m.auto = types.SimpleNamespace(SendKeys=lambda s, **kw: typed.append(s))
w._do_send()
check("no window -> nothing typed", typed == [])
check("no window -> retry armed", w.state == MonitorWorker.ARMED and w.retries == 1)

print()
if FAILURES:
    print(f"{len(FAILURES)} test(s) FAILED")
    raise SystemExit(1)
print("all tests passed")
