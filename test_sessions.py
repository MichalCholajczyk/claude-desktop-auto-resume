"""Regression tests for the production multi-conversation path (no real input)."""
import datetime as dt
import queue
import types
import unittest
from unittest.mock import patch, Mock

import claude_auto_continue as app
from session_automation import (Node, Pane, SessionEngine, SessionState, ClaudeUI,
                                choose_reset, discover, final_limit_card, question_in,
                                signals, usage_meter)

NOW = dt.datetime(2026, 9, 9, 14, 0)

# The live card Claude Code appends when a request hits the limit (captured 15.09).
LIMIT_CARD = (("Session limit reached", "TextControl"),
              ("Try again after your session limit resets.", "TextControl"),
              ("View details", "ButtonControl"), ("Try again", "ButtonControl"),
              ("Show message actions", "ButtonControl"))


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


def message(p, number, *children):
    """A transcript article ("Message N") holding (name, control type) children."""
    feed = next(n for n in p.root.walk() if n.name == "Chat messages")
    article = node(f"Message {number}", parent=feed)
    article.role = "article"
    for name, kind in children:
        node(name, kind, article)
    return article


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

    def test_limit_card_ending_the_transcript_is_a_limit(self):
        p = pane()
        message(p, 1, ("You said: implement the spec", "TextControl"))
        message(p, 2, *LIMIT_CARD)
        state = signals(p, app.detect_limit_banner)
        self.assertTrue(state["limit"])
        self.assertIsNone(state["reset"])
        self.assertFalse(state["error"])

    def test_limit_card_hint_with_time_gives_the_reset(self):
        p = pane()
        message(p, 1, ("Session limit reached", "TextControl"),
                ("Your session limit resets at 9:30 AM. Try again then.", "TextControl"),
                ("View details", "ButtonControl"), ("Try again", "ButtonControl"))
        found, reset = final_limit_card(p, app.parse_reset_time)
        self.assertTrue(found)
        self.assertEqual((reset.hour, reset.minute), (9, 30))

    def test_collapsed_limit_card_is_a_limit(self):
        p = pane()
        message(p, 1, ("Session limit reached", "ButtonControl"), ("Show message actions", "ButtonControl"))
        self.assertTrue(signals(p, app.detect_limit_banner)["limit"])

    def test_limit_card_before_newer_messages_is_history(self):
        p = pane()
        message(p, 1, *LIMIT_CARD)
        message(p, 2, ("You said: continue", "TextControl"))
        self.assertFalse(signals(p, app.detect_limit_banner)["limit"])
        message(p, 3, ("Done — the save bug is fixed.", "TextControl"))
        self.assertFalse(signals(p, app.detect_limit_banner)["limit"])

    def test_limit_wording_in_messages_is_not_a_card(self):
        p = pane()
        message(p, 1, ("Session limit reached", "TextControl"),
                ("The card then says the session limit resets later.", "TextControl"))
        self.assertFalse(signals(p, app.detect_limit_banner)["limit"])
        q = pane()
        message(q, 1, ("You said: Session limit reached", "TextControl"),
                ("Session limit reached", "TextControl"), ("Try again", "ButtonControl"))
        self.assertFalse(signals(q, app.detect_limit_banner)["limit"])

    def test_usage_meter_label_forms(self):
        p = pane()
        node("Usage: Context 150k, 100% of 5-hour limit, Resets at 9:30 AM", "ButtonControl", p.root)
        meter = usage_meter(p, app.parse_reset_time)
        self.assertEqual(meter["pct"], 100)
        self.assertEqual((meter["reset"].hour, meter["reset"].minute), (9, 30))
        q = pane()
        node("Usage limit reached", "ButtonControl", q.root)
        node("Usage, Weekly · all models: 19%, Resets Mon 6:00 PM", "ButtonControl", q.root)
        meter = usage_meter(q, app.parse_reset_time)
        self.assertEqual(meter["pct"], 19)
        self.assertEqual((meter["reset"].weekday(), meter["reset"].hour), (0, 18))
        self.assertEqual(usage_meter(pane(), app.parse_reset_time), dict(pct=None, reset=None))
        r = pane()   # a full context window is not a plan limit
        node("Usage: context 100%, Weekly · all models: 19%", "ButtonControl", r.root)
        self.assertEqual(usage_meter(r, app.parse_reset_time)["pct"], 19)

    def test_choose_reset_prefers_the_latest_future_time(self):
        hour = dt.timedelta(hours=1)
        self.assertEqual(choose_reset([None, NOW + hour, NOW + 2 * hour, NOW - hour], NOW), NOW + 2 * hour)
        self.assertEqual(choose_reset([NOW - 2 * hour, None, NOW - hour], NOW), NOW - hour)
        self.assertIsNone(choose_reset([None, None], NOW))

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
        s = SessionState("waiting", "limit", NOW, reset=NOW - dt.timedelta(seconds=60))
        self.engine.step("code:Alpha", s, observed(), NOW)
        self.assertEqual(len(self.ui.calls), 1)

    def test_busy_session_does_not_receive_continue(self):
        s = SessionState("waiting", "limit", NOW, reset=NOW - dt.timedelta(seconds=60))
        self.engine.step("code:Alpha", s, observed(busy=True), NOW)
        self.assertFalse(self.ui.calls)
        self.assertEqual(s.phase, "watching")

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
        armed = SessionState("waiting", "limit", NOW, reset=NOW - dt.timedelta(seconds=60))
        self.engine.step("code:A", armed, observed(), NOW)
        self.assertEqual(armed.phase, "exhausted")
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


def claude_question(p, labels, multi=False, primary="Submit", answered=False):
    """The question card as Claude 1.5238x exposes it (captured 15.09): option
    buttons hold a label, a description and a digit key. Single choice exposes no
    selection state (a click answers at once); multiple choice toggles aria-pressed."""
    group = node(parent=p.root)
    node("Co znaczy „ile serii z rzędu”? Silnik dziś nie liczy serii.", "TextControl", group)
    node("View question options", "ButtonControl", group)
    node("Dismiss question", "ButtonControl", group)
    for number, label in enumerate([*labels, "Other"], 1):
        button = node(f"{label} opis {number}", "ButtonControl", group)
        node(label, "TextControl", node(parent=button))
        if label != "Other":
            node("opis", "TextControl", node(parent=button))
        node(str(number), "TextControl", button)
        state = types.SimpleNamespace(ToggleState=0)
        button.control = types.SimpleNamespace(
            GetTogglePattern=(lambda s=state: s) if multi else (lambda: None), IsEnabled=True)
    node("Other option", "EditControl", group)
    node("Skip", "ButtonControl", group)
    submit = node(primary, "ButtonControl", group)
    submit.control = types.SimpleNamespace(IsEnabled=answered)
    return group


class QuestionAnswerTests(unittest.TestCase):
    """Auto-answering both kinds of Claude question cards."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.pane = pane("Onboarding i wskaźniki ćwiczeń")
        self.ui = ClaudeUI(self.worker)
        self.ui.resolve = lambda *a, **kw: self.pane
        self.ui.value = lambda n: ""
        self.calls = []
        patcher = patch("session_automation.time.sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def clicks_answer_at_once(self, n):
        """Single choice: Claude records the clicked option and the card goes away."""
        self.calls.append(n.name.split(" opis ")[0])
        if n.type == "ButtonControl" and n.name.split(" opis ")[0] not in ("Other", "Submit", "Next"):
            self.pane.root.children = [c for c in self.pane.root.children if not any(
                b.name == "Dismiss question" for b in c.children)]

    def toggles(self, n):
        """Multiple choice: an option flips aria-pressed; Submit / Next only records the click."""
        self.calls.append(n.name.split(" opis ")[0])
        pattern = getattr(n.control, "GetTogglePattern", lambda: None)()
        if pattern is not None:
            pattern.ToggleState = 1 - pattern.ToggleState

    def answer(self):
        return self.ui.answer(self.pane.key, question_in(self.pane)["fingerprint"])

    def test_single_choice_clicks_only_the_recommended_option(self):
        claude_question(self.pane, ("Serie z recepty (Recommended)", "Wszystkie serie na górze", "Z notatki trenera"))
        self.ui.click = self.clicks_answer_at_once
        self.assertTrue(self.answer())
        self.assertEqual(self.calls, ["Serie z recepty (Recommended)"])

    def test_single_choice_click_that_did_not_register_is_reported(self):
        claude_question(self.pane, ("Serie z recepty (Recommended)", "Wszystkie serie na górze"))
        self.ui.click = self.calls.append          # the card stays: nothing was accepted
        with self.assertRaisesRegex(RuntimeError, "not accepted"):
            self.answer()
        self.assertEqual(len(self.calls), 1)

    def test_single_choice_without_one_recommendation_asks_claude_in_other(self):
        for labels in (("Szybko", "Wolno"), ("Szybko (Recommended)", "Wolno (Recommended)")):
            with self.subTest(labels=labels):
                self.pane = pane("Onboarding i wskaźniki ćwiczeń")
                claude_question(self.pane, labels)
                self.calls.clear()
                self.ui.click = self.clicks_answer_at_once
                self.ui.type_into = lambda n, text: self.calls.append((n.name, text))
                self.assertTrue(self.answer())
                self.assertEqual(self.calls, ["Other", ("Other option", "Pick your recommended option(s)."), "Submit"])

    def test_multiple_choice_ticks_each_recommendation_then_next(self):
        claude_question(self.pane, ("Siła (Recommended)", "Masa", "Wytrzymałość (Recommended)"),
                        multi=True, primary="Next")
        self.ui.click = self.toggles
        self.assertTrue(self.answer())
        self.assertEqual(self.calls, ["Siła (Recommended)", "Wytrzymałość (Recommended)", "Next"])

    def test_started_answers_are_left_alone(self):
        claude_question(self.pane, ("Serie z recepty (Recommended)", "Wszystkie serie na górze"), answered=True)
        self.ui.click = self.calls.append
        with self.assertRaisesRegex(RuntimeError, "already has a selected answer"):
            self.answer()
        self.pane = pane("Onboarding i wskaźniki ćwiczeń")
        group = claude_question(self.pane, ("Siła (Recommended)", "Masa"), multi=True)
        group.children[3].control.GetTogglePattern().ToggleState = 1
        with self.assertRaisesRegex(RuntimeError, "already has a selected answer"):
            self.answer()
        self.assertEqual(self.calls, [])

    def test_question_card_with_next_button_is_found(self):
        claude_question(self.pane, ("Siła (Recommended)", "Masa"), primary="Next")
        found = question_in(self.pane)
        self.assertEqual(found["submit"].name, "Next")
        self.assertEqual([label for label in found["labels"]], ["Siła (Recommended)", "Masa", "Other"])
        self.assertEqual(len(found["recommended"]), 1)

    def test_answer_that_failed_before_any_click_is_tried_again(self):
        engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())
        self.worker.cfg.update(auto_approach=True, auto_send=True)
        claude_question(self.pane, ("Serie z recepty (Recommended)", "Wszystkie serie na górze"))
        asked = observed(question=question_in(self.pane))
        state, attempts = SessionState(), []

        def not_foreground(key, fingerprint):
            attempts.append(fingerprint)
            raise RuntimeError("Claude is not foreground")
        self.ui.answer = not_foreground
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                engine.step(self.pane.key, state, asked, NOW)
        self.assertEqual(len(attempts), 2)

        def clicked_then_failed(key, fingerprint):
            attempts.append(fingerprint)
            self.ui.clicks += 1
            raise RuntimeError("Recommended selections could not be verified")
        self.ui.answer = clicked_then_failed
        for _ in range(2):
            try:
                engine.step(self.pane.key, state, asked, NOW)
            except RuntimeError:
                pass
        self.assertEqual(len(attempts), 3)      # an uncertain click is never repeated


class FakeQuota:
    def __init__(self, reset=None):
        self.reset = reset

    def latest_reset(self):
        return self.reset


class LimitSafetyTests(unittest.TestCase):
    """A limit continuation must never be typed while the conversation is still blocked."""

    def setUp(self):
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG))
        self.worker.log = lambda *a, **kw: None
        self.ui = FixtureUI()
        self.engine = SessionEngine(self.worker, self.ui, quota=FakeQuota())

    def test_unknown_reset_is_never_sent_while_the_limit_is_visible(self):
        # 15.09: the card had no reset time; the old engine typed at 09:03, 09:14 and
        # 09:25 into a session that stayed blocked until 09:30.
        start, blocked, state = dt.datetime(2026, 9, 15, 8, 50), observed(limit=True), SessionState()
        now = start
        while now < start + dt.timedelta(hours=5, seconds=60):
            self.engine.step("code:Podprojekt C", state, blocked, now)
            now += dt.timedelta(minutes=1)
        self.assertEqual(self.ui.calls, [])
        self.assertEqual((state.phase, state.attempts), ("waiting", 0))
        # A 5-hour limit cannot outlast its window: one attempt at the cap.
        self.engine.step("code:Podprojekt C", state, blocked, start + dt.timedelta(hours=5, seconds=60))
        self.assertEqual(len(self.ui.calls), 1)

    def test_unknown_reset_sends_once_the_limit_is_gone_on_two_scans(self):
        state, minute = SessionState(), dt.timedelta(minutes=1)
        self.engine.step("code:C", state, observed(limit=True), NOW)
        self.engine.step("code:C", state, observed(), NOW + minute)
        self.engine.step("code:C", state, observed(limit=True), NOW + 2 * minute)
        self.engine.step("code:C", state, observed(), NOW + 3 * minute)
        self.assertEqual(self.ui.calls, [])
        self.engine.step("code:C", state, observed(), NOW + 4 * minute)
        self.assertEqual(len(self.ui.calls), 1)

    def test_reset_found_while_waiting_moves_the_send(self):
        state, reset = SessionState(), NOW + dt.timedelta(minutes=40)
        self.engine.step("code:C", state, observed(limit=True), NOW)
        self.engine.step("code:C", state, observed(limit=True, reset=reset), NOW + dt.timedelta(minutes=1))
        self.assertEqual(state.due, reset + dt.timedelta(seconds=60))
        self.engine.step("code:C", state, observed(limit=True), reset)
        self.assertEqual(self.ui.calls, [])
        self.engine.step("code:C", state, observed(limit=True), reset + dt.timedelta(seconds=60))
        self.assertEqual(len(self.ui.calls), 1)

    def test_known_reset_sends_even_if_the_card_is_still_shown(self):
        state, reset = SessionState(), NOW + dt.timedelta(minutes=40)
        self.engine.step("code:C", state, observed(limit=True, reset=reset), NOW)
        self.engine.step("code:C", state, observed(limit=True), reset + dt.timedelta(seconds=59))
        self.assertEqual(self.ui.calls, [])
        self.engine.step("code:C", state, observed(limit=True), reset + dt.timedelta(seconds=60))
        self.assertEqual(len(self.ui.calls), 1)

    def test_still_blocked_after_a_past_reset_waits_before_trying_again(self):
        reset, state = NOW - dt.timedelta(minutes=1), SessionState()
        self.engine.step("code:C", state, observed(limit=True, reset=reset), NOW)
        self.assertEqual(len(self.ui.calls), 1)    # the reset already passed: resume now
        self.engine.step("code:C", state, observed(limit=True, reset=reset), NOW + dt.timedelta(seconds=61))
        self.assertEqual(len(self.ui.calls), 1)
        self.assertEqual(state.due, NOW + dt.timedelta(seconds=self.worker.cfg["retry_wait_s"]))
        self.engine.step("code:C", state, observed(limit=True, reset=reset), state.due)
        self.assertEqual(len(self.ui.calls), 2)

    def test_scan_takes_the_reset_from_claude_code_logs(self):
        p = pane("Podprojekt C")
        message(p, 1, ("You said: continue", "TextControl"))
        message(p, 2, *LIMIT_CARD)
        self.ui.snapshot = lambda: p.root
        self.ui.resolve = lambda key, navigate=True: discover(p.root)[0][0]
        # Unit tests must never reach the real Claude window.
        self.worker._get_window = Mock(return_value=None)
        self.worker._read_usage_panel = Mock(side_effect=AssertionError("usage panel opened"))
        engine = SessionEngine(self.worker, self.ui, quota=FakeQuota(dt.datetime(2026, 9, 15, 9, 30)))
        engine.tick(now=dt.datetime(2026, 9, 15, 8, 50))
        state = engine.sessions["code:Podprojekt C"]
        self.assertEqual((state.phase, state.reason), ("waiting", "limit"))
        self.assertEqual(state.due, dt.datetime(2026, 9, 15, 9, 31))
        self.assertEqual(self.ui.calls, [])


class WorkerStartTests(unittest.TestCase):
    """Start watching gives a visible countdown before the app touches Claude."""

    def setUp(self):
        self.clock = [1000.0]
        patcher = patch.object(app.time, "monotonic", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.worker = app.MonitorWorker(queue.Queue(), dict(app.DEFAULT_CONFIG, keep_awake=False))
        self.worker.log = Mock()
        self.worker.hwnd = 101
        self.worker.engine = Mock()

    def command(self, name, payload=None, seconds=0.0):
        self.worker.command(name, payload)
        self.worker._process_commands()
        self.clock[0] += seconds

    def test_start_counts_down_before_scanning(self):
        self.command("start", seconds=9.9)
        self.assertEqual(self.worker.state, "STARTING")
        self.assertEqual(self.worker._state_info()["start_until"], self.worker.start_until)
        self.worker._tick()
        self.worker.engine.tick.assert_not_called()
        self.clock[0] += 0.1
        self.worker._tick()
        self.assertEqual(self.worker.state, "MONITORING")
        self.worker._tick()
        self.worker.engine.tick.assert_called_once_with()

    def test_stop_during_countdown_cancels_it(self):
        self.command("start", seconds=3)
        self.command("stop", seconds=30)
        self.worker._tick()
        self.assertEqual(self.worker.state, "IDLE")
        self.worker.engine.tick.assert_not_called()
        self.worker.engine.publish.assert_called()

    def test_zero_countdown_starts_immediately(self):
        self.worker.cfg["start_delay_s"] = 0
        self.command("start")
        self.assertEqual(self.worker.state, "MONITORING")

    def test_manual_arm_while_idle_waits_for_the_countdown(self):
        reset = dt.datetime(2026, 9, 15, 9, 30)
        self.command("arm_manual", reset, seconds=5)
        self.worker._tick()
        self.assertEqual(self.worker.state, "STARTING")
        self.worker.engine.arm.assert_not_called()
        self.clock[0] += 5
        self.worker._tick()
        self.assertEqual(self.worker.state, "MONITORING")
        self.worker.engine.arm.assert_called_once_with(reset)

    def test_rows_and_header_show_the_countdown(self):
        engine = SessionEngine(self.worker, FixtureUI(), quota=FakeQuota())
        engine.catalog = {"code:A": dict(key="code:A", title="A", kind="code", source="open", available=True)}
        self.worker.state, self.worker.start_until = "STARTING", NOW
        engine.publish()
        events = {}
        while not self.worker.out.empty():
            kind, data = self.worker.out.get_nowait()
            events[kind] = data
        self.assertEqual(events["chats"][0]["phase"], "starting")
        self.assertEqual((events["state"]["state"], events["state"]["start_until"]), ("STARTING", NOW))


if __name__ == "__main__":
    unittest.main()
