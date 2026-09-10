"""Regression tests for the production multi-conversation path (no real input)."""
import datetime as dt
import queue
import types
import unittest
from unittest.mock import patch, Mock

import claude_auto_continue as app
from session_automation import (Node, Pane, SessionEngine, SessionState, ClaudeUI,
                                discover, question_in, signals)

NOW = dt.datetime(2026, 9, 9, 14, 0)


def node(name="", kind="GroupControl", parent=None):
    n = Node(name, kind, (10, 10, 300, 300), parent=parent)
    if parent:
        parent.children.append(n)
    return n


def pane(title="Alpha", parent=None, kind="session"):
    root = node(parent=parent)
    header = node(parent=root)
    node(title + ", rename " + kind, "ButtonControl", header)
    body = node(parent=root)
    node("Chat messages", parent=body)
    prompt = node("Prompt", "EditControl", body)
    return Pane(("code:" if kind == "session" else "chat:") + title, title,
                "code" if kind == "session" else "chat", root, prompt)


def question(p, labels=("Fast (Recommended)", "Slow", "Other")):
    group = node(parent=p.root)
    node("Choose approach", "TextControl", group)
    node("Dismiss question", "ButtonControl", group)
    for label in labels:
        button = node(label, "ButtonControl", group)
        button.control = types.SimpleNamespace(GetTogglePattern=lambda: types.SimpleNamespace(ToggleState=0))
        node(label, "TextControl", button)
    node("Other option", "EditControl", group)
    node("Skip", "ButtonControl", group)
    node("Submit", "ButtonControl", group)
    return group


def observed(**kwargs):
    return dict(limit=False, reset=None, error=None, permanent=False, retry=None,
                question=None, busy=False, **{}) | kwargs


class FixtureUI:
    def __init__(self):
        self.calls = []

    def resume(self, key, prefer_retry, api_error):
        self.calls.append((key, prefer_retry, api_error))
        return "message"

    def answer(self, key, fingerprint):
        self.calls.append((key, fingerprint))
        return True


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = FixtureUI()
        self.engine = SessionEngine(self.worker, self.ui)

    def test_window_refresh_also_refreshes_new_conversations(self):
        self.worker._enum_windows = lambda: [(101, "Claude")]
        self.worker.hwnd = 101
        self.worker.engine.refresh = Mock()
        self.worker.command("refresh_windows")
        self.worker._process_commands()
        self.worker.engine.refresh.assert_called_once_with()

    def test_refresh_follows_latest_sidebar_order_and_keeps_saved_chats(self):
        root = node()
        bar = node("Sidebar", parent=root)
        def add_chat(title):
            row = node(parent=bar)
            node(title, "ButtonControl", row)
            node("More options for " + title, "ButtonControl", row)
            return row
        older = add_chat("Older")
        oldest = add_chat("Oldest")
        pane("Oldest", root)  # An open split must not override sidebar order.
        self.ui.snapshot = lambda: root
        self.worker.cfg["selected_chats"] = ["chat:Saved"]
        self.engine.refresh()
        newest = add_chat("Newest")
        bar.children = [newest, older, oldest]
        self.engine.refresh()
        self.assertEqual(list(self.engine.catalog),
                         ["code:Newest", "code:Older", "code:Oldest", "chat:Saved"])
        self.assertEqual(self.engine.catalog["code:Oldest"]["source"], "open")
        self.assertFalse(self.engine.catalog["chat:Saved"]["available"])
        bar.children = [older, newest, oldest]
        self.engine.refresh()
        self.assertEqual(list(self.engine.catalog)[:3],
                         ["code:Older", "code:Newest", "code:Oldest"])

    def test_new_pane_missing_from_sidebar_appears_before_cached_chats(self):
        root = node()
        pane("Older", root)
        self.ui.snapshot = lambda: root
        self.engine.refresh()
        root.children.clear()
        pane("Newest", root)
        self.engine.refresh()
        self.assertEqual(list(self.engine.catalog), ["code:Newest", "code:Older"])
        self.assertFalse(self.engine.catalog["code:Older"]["available"])

    def test_explicit_refresh_reconnects_after_claude_restarts(self):
        self.worker._enum_windows = lambda: [(202, "Claude")]
        self.worker.hwnd = 101
        self.worker.engine.refresh = Mock()
        self.worker.command("refresh_windows")
        self.worker._process_commands()
        self.assertEqual(self.worker.hwnd, 202)
        self.worker.engine.refresh.assert_called_once_with()

    def test_split_panes_keep_composer_and_not_browser(self):
        root = node()
        first, second = pane("Alpha", root), pane("Beta", root)
        browser = node(parent=root)
        node("Page URL", "EditControl", browser)
        found, _ = discover(root)
        self.assertEqual([p.key for p in found], ["code:Alpha", "code:Beta"])
        self.assertIs(found[0].root, first.root)
        self.assertIs(found[1].prompt, second.prompt)

    def test_no_title_never_becomes_a_target(self):
        root = node()
        node("Prompt", "EditControl", root)
        self.assertEqual(discover(root)[0], [])

    def test_duplicate_titles_are_ambiguous(self):
        root = node()
        pane("Same", root)
        pane("Same", root)
        ui = ClaudeUI(self.worker)
        ui.snapshot = lambda: root
        self.assertIsNone(ui.resolve("code:Same"))

    def test_sidebar_uses_row_not_more_options(self):
        root, bar = node(), node("Sidebar")
        root.children.append(bar)
        bar.parent = root
        row = node(parent=bar)
        target = node("Awaiting input Alpha", "ButtonControl", row)
        group = node(parent=row)
        node("More options for Alpha", "ButtonControl", group)
        entries = discover(root)[1]
        self.assertEqual(entries[0]["key"], "code:Alpha")
        self.assertIs(entries[0]["node"], target)

    def test_recommendation_english_polish_multiple(self):
        p = pane()
        question(p, ("One (Recommended)", "Dwa (rekomendacja)", "Other"))
        self.assertEqual(len(question_in(p)["recommended"]), 2)

    def test_recommendation_in_description_not_label(self):
        p = pane()
        group = question(p, ("Fast", "Slow", "Other"))
        first = group.children[2]
        first.name += " recommended in certain cases"
        node("Recommended in certain cases", "TextControl", first)
        self.assertEqual(question_in(p)["recommended"], [])

    def test_not_recommended_is_not_a_recommendation(self):
        p = pane()
        question(p, ("Fast (not recommended)", "Slow", "Other"))
        self.assertFalse(question_in(p)["recommended"])

    def test_question_only_in_live_widget(self):
        p = pane()
        group = question(p)
        p.root.children.remove(group)
        chat = next(n for n in p.root.walk() if n.name == "Chat messages")
        group.parent = chat
        chat.children.append(group)
        self.assertIsNone(question_in(p))

    def test_only_real_retry_button(self):
        p = pane()
        node("Try again", "TextControl", p.root)
        self.assertIsNone(signals(p, app.detect_limit_banner)["retry"])
        retry = node("Try again", "ButtonControl", p.root)
        self.assertIs(signals(p, app.detect_limit_banner)["retry"], retry)

    def test_api_error_and_account_error(self):
        p = pane()
        error = node('API Error: 529 overloaded_error', "TextControl", p.root)
        self.assertTrue(signals(p, app.detect_limit_banner)["error"])
        error.name = "API Error: 401 authentication_error"
        self.assertTrue(signals(p, app.detect_limit_banner)["permanent"])

    def test_titles_drafts_and_question_labels_are_not_limit_notices(self):
        p = pane("Usage limit reached")
        node("Usage limit reached", "TextControl", p.prompt)
        question(p, ("API Error: 401", "Usage limit reached", "Other"))
        state = signals(p, app.detect_limit_banner)
        self.assertFalse(state["limit"])
        self.assertFalse(state["error"])
        self.assertTrue(state["question"])

    def test_quoted_error_is_not_a_live_failure(self):
        p = pane()
        chat = next(n for n in p.root.walk() if n.name == "Chat messages")
        code = node(parent=chat)
        code.role = "code"
        node("API Error: 529", "TextControl", code)
        self.assertFalse(signals(p, app.detect_limit_banner)["error"])

    def test_old_api_error_in_transcript_does_not_retrigger(self):
        p = pane()
        chat = next(n for n in p.root.walk() if n.name == "Chat messages")
        node("API Error: 529", "TextControl", chat)
        node("Finished the work", "TextControl", chat)
        self.assertFalse(signals(p, app.detect_limit_banner)["error"])

    def test_two_resets_send_to_both_original_conversations(self):
        for key in ("code:Alpha", "code:Beta"):
            state = SessionState()
            self.engine.step(key, state, observed(limit=True, reset=NOW), NOW)
            self.engine.step(key, state, observed(), NOW + dt.timedelta(seconds=61))
            self.assertEqual(state.phase, "verifying")
        self.assertEqual([c[0] for c in self.ui.calls], ["code:Alpha", "code:Beta"])

    def test_clear_limit_at_send_time_still_sends(self):
        s = SessionState("waiting", "limit", NOW)
        self.engine.step("code:Alpha", s, observed(), NOW)
        self.assertEqual(len(self.ui.calls), 1)

    def test_busy_session_does_not_receive_continue(self):
        s = SessionState("waiting", "limit", NOW)
        self.engine.step("code:Alpha", s, observed(busy=True), NOW)
        self.assertFalse(self.ui.calls)

    def test_api_delay_backoff_and_cap(self):
        self.worker.cfg["max_retries"] = 2
        s = SessionState()
        error = observed(error="API Error: 529")
        self.engine.step("code:A", s, error, NOW)
        self.assertEqual(s.due, NOW + dt.timedelta(seconds=30))
        self.engine.step("code:A", s, error, s.due)
        self.assertEqual(len(self.ui.calls), 1)
        self.engine.step("code:A", s, error, s.due)
        self.assertEqual(s.due, NOW + dt.timedelta(seconds=120))
        self.engine.step("code:A", s, error, s.due)
        self.engine.step("code:A", s, error, s.due)
        self.engine.step("code:A", s, error, s.due)
        self.assertEqual(s.phase, "exhausted")
        self.assertEqual(len(self.ui.calls), 2)

    def test_api_error_clearing_before_retry_cancels_send(self):
        s = SessionState("waiting", "api", NOW)
        self.engine.step("code:A", s, observed(), NOW)
        self.assertFalse(self.ui.calls)

    def test_disabled_api_retry_cancels_pending_action(self):
        self.worker.cfg["retry_api_errors"] = False
        s = SessionState("waiting", "api", NOW)
        self.engine.step("code:A", s, observed(error="API Error: 529"), NOW)
        self.assertFalse(self.ui.calls)

    def test_usage_notice_supersedes_short_api_retry(self):
        s = SessionState("waiting", "api", NOW)
        reset = NOW + dt.timedelta(hours=2)
        self.engine.step("code:A", s, observed(limit=True, reset=reset), NOW)
        self.assertEqual(s.reason, "limit")
        self.assertEqual(s.due, reset + dt.timedelta(seconds=60))
        self.assertFalse(self.ui.calls)

    def test_new_authentication_error_cancels_retry(self):
        s = SessionState("waiting", "api", NOW)
        self.engine.step("code:A", s, observed(error="API Error: 401", permanent=True), NOW)
        self.assertEqual(s.phase, "exhausted")
        self.assertFalse(self.ui.calls)

    def test_cleared_question_does_not_leave_overdue_timer(self):
        s = SessionState("waiting", "limit", NOW)
        self.engine.step("code:A", s, observed(question={"fingerprint": "q"}), NOW)
        self.assertEqual(s.phase, "question")
        self.assertIsNone(s.due)
        self.engine.step("code:A", s, observed(), NOW)
        self.assertEqual(s.phase, "watching")

    def test_auto_send_off_blocks_resume_and_approach(self):
        self.worker.cfg.update(auto_send=False, auto_approach=True)
        self.engine.step("code:A", SessionState("waiting", "limit", NOW), observed(), NOW)
        self.engine.step("code:A", SessionState(), observed(question={"fingerprint": "q"}), NOW)
        self.assertFalse(self.ui.calls)

    def test_approach_opt_in_and_deduplication(self):
        s, q = SessionState(), observed(question={"fingerprint": "q"})
        self.engine.step("code:A", s, q, NOW)
        self.assertFalse(self.ui.calls)
        self.worker.cfg["auto_approach"] = True
        self.engine.step("code:A", s, q, NOW)
        self.engine.step("code:A", s, q, NOW)
        self.assertEqual(self.ui.calls, [("code:A", "q")])

    def test_sessions_have_independent_retry_budgets(self):
        self.worker.cfg["max_retries"] = 1
        exhausted = SessionState("waiting", "api", NOW, attempts=1)
        available = SessionState("waiting", "api", NOW)
        error = observed(error="API Error: 529")
        self.engine.step("code:A", exhausted, error, NOW)
        self.engine.step("code:B", available, error, NOW)
        self.assertEqual([c[0] for c in self.ui.calls], ["code:B"])

    def test_retry_preference_and_api_use_button(self):
        p = pane()
        button = node("Try again", "ButtonControl", p.root)
        ui = ClaudeUI(self.worker)
        ui.resolve = lambda *a, **kw: p
        ui.value = lambda n: ""
        clicks = []
        ui.click = clicks.append
        self.assertEqual(ui.resume(p.key, prefer_retry=True), "retry")
        self.assertEqual(ui.resume(p.key, api_error=True), "retry")
        self.assertEqual(clicks, [button, button])

    def test_draft_is_not_overwritten_even_with_retry(self):
        p = pane()
        node("Try again", "ButtonControl", p.root)
        ui = ClaudeUI(self.worker)
        ui.resolve = lambda *a, **kw: p
        ui.value = lambda n: "My draft"
        with self.assertRaisesRegex(RuntimeError, "Existing draft"):
            ui.resume(p.key, prefer_retry=True)

    def test_missing_target_does_not_fallback_to_window_center(self):
        ui = ClaudeUI(self.worker)
        ui.resolve = lambda *a, **kw: None
        with self.assertRaisesRegex(RuntimeError, "missing or ambiguous"):
            ui.resume("code:Missing")

    def test_other_field_fallback_and_submit(self):
        p = pane()
        question(p, ("Fast", "Slow", "Other"))
        ui = ClaudeUI(self.worker)
        ui.resolve = lambda *a, **kw: p
        ui.value = lambda n: ""
        calls = []
        ui.click = lambda n: calls.append(n.name)
        ui.type_into = lambda n, text: calls.append((n.name, text))
        self.assertTrue(ui.answer(p.key, question_in(p)["fingerprint"]))
        self.assertEqual(calls, ["Other", ("Other option", "Pick your recommended option(s)."), "Submit"])

    def test_no_fallback_to_fourth_option(self):
        p = pane()
        group = question(p, ("One", "Two", "Three", "Four"))
        group.children = [n for n in group.children if n.type != "EditControl"]
        ui = ClaudeUI(self.worker)
        ui.resolve = lambda *a, **kw: p
        ui.value = lambda n: ""
        with self.assertRaisesRegex(RuntimeError, "No recommended"):
            ui.answer(p.key, question_in(p)["fingerprint"])


if __name__ == "__main__":
    unittest.main()
