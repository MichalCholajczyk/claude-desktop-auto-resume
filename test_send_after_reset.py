"""Regression for the reset bug, through the production session scheduler.

At reset + 60s the banner is normally gone. Its disappearance must not cancel
an armed continuation or redirect it to the newly active conversation.
Run: py test_send_after_reset.py
"""
import datetime as dt
import queue
import unittest

import claude_auto_continue as app
from session_automation import SessionEngine, SessionState
from test_sessions import FixtureUI, observed


class ResetSendTests(unittest.TestCase):
    def test_armed_conversation_sends_after_banner_clears(self):
        worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        worker.log = lambda *args, **kwargs: None
        ui = FixtureUI()
        engine = SessionEngine(worker, ui)
        now = dt.datetime(2026, 9, 9, 14, 0)
        for signal in (observed(), observed(limit=True), observed(error="API Error: 529")):
            state = SessionState("waiting", "limit", now)
            engine.step("code:Original conversation", state, signal, now)
            self.assertEqual(state.phase, "verifying")
            self.assertEqual(ui.calls[-1][0], "code:Original conversation")

    def test_failed_send_cannot_be_reported_as_success(self):
        worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        ui = FixtureUI()
        def fail(*args):
            raise RuntimeError("Window unavailable")
        ui.resume = fail
        engine = SessionEngine(worker, ui)
        now = dt.datetime(2026, 9, 9, 14, 0)
        state = SessionState("waiting", "limit", now)
        with self.assertRaises(RuntimeError):
            engine.step("code:Original conversation", state, observed(), now)
        self.assertEqual(state.phase, "waiting")
        self.assertEqual(state.attempts, 1)
        self.assertGreater(state.due, now)


if __name__ == "__main__":
    unittest.main()
