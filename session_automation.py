"""Scoped Claude accessibility adapter and independent per-conversation scheduler.

No coordinates or UIA objects are persisted. Every action resolves its conversation
again; ambiguous titles, drafts and unknown controls fail closed.
"""
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import re
import time


PROMPTS = {"prompt", "write your prompt to claude", "reply to claude", "message claude"}
RETRY = {"try again", "retry", "spróbuj ponownie", "ponów"}
SUBMIT = {"submit", "wyślij", "zatwierdź"}
OTHER = {"other", "inne", "inna", "inny"}
RECOMMENDED = re.compile(r"\b(?:recommended|rekomendowan\w*|rekomendacja|zalecan\w*|polecan\w*)\b", re.I)
NEGATIVE = re.compile(r"\b(?:not\s+recommended|nie\s+(?:zalecan\w*|polecan\w*|rekomendowan\w*))\b", re.I)
API_ERROR = re.compile(
    r"^(?:API\s*Error\b|API\s*error:|Error:\s*(?:5\d\d|429)\b|"
    r"overloaded_error\b|internal_server_error\b|"
    r"(?:the\s+)?server\s+is\s+(?:overloaded|busy)|"
    r"Claude is (?:currently )?(?:overloaded|experiencing)|"
    r"Something went wrong|An error occurred|Network error|"
    r"Connection error|Request timed out|Serwer jest przeciążony|Błąd API)", re.I)
PERMANENT_ERROR = re.compile(r"\b(?:400|401|402|403|404|413|authentication_error|permission_error|billing_error|invalid_request_error)\b", re.I)


def key_for(kind, title):
    return kind + ":" + title.strip()


@dataclass(eq=False)
class Node:
    name: str
    type: str
    rect: tuple = (0, 0, 0, 0)
    control: object = None
    parent: object = None
    children: list = field(default_factory=list)
    role: str = ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node:
            yield node
            node = node.parent

    def inside(self, name):
        return any(n.name.casefold() == name for n in self.ancestors())

    @property
    def visible(self):
        l, t, r, b = self.rect
        return r > l and b > t


@dataclass
class Pane:
    key: str
    title: str
    kind: str
    root: Node
    prompt: Node

    def ui_nodes(self):
        return [n for n in self.root.walk() if not n.inside("chat messages")]


def rename_title(node):
    if node.type != "ButtonControl":
        return None
    match = re.fullmatch(r"(.+), rename (session|chat|task)", node.name)
    return (match[1], "code" if match[2] == "session" else "chat") if match else None


def discover(root):
    """Return named panes and sidebar entries. Duplicate keys remain ambiguous."""
    nodes = list(root.walk())
    prompts = [n for n in nodes if n.type == "EditControl" and
               n.name.casefold() in PROMPTS and n.visible]
    panes = []
    for prompt in prompts:
        for ancestor in prompt.ancestors():
            descendants = list(ancestor.walk())
            if sum(p in descendants for p in prompts) != 1:
                break
            titles = [rename_title(n) for n in descendants if rename_title(n)]
            if len(titles) == 1:
                title, kind = titles[0]
                panes.append(Pane(key_for(kind, title), title, kind, ancestor, prompt))
                break
    sidebar = [n for n in nodes if n.name.casefold() == "sidebar"]
    entries = []
    for bar in sidebar:
        bar_nodes = list(bar.walk())
        kind = "chat" if any(n.name == "Chats and tasks" for n in bar_nodes) else "code"
        # Claude exposes the full title in the companion menu button, even when
        # the displayed text is truncated. The row itself is a different button.
        for menu in bar_nodes:
            if menu.type != "ButtonControl" or not menu.name.startswith("More options for "):
                continue
            title = menu.name[len("More options for "):]
            row = None
            for ancestor in menu.ancestors():
                if ancestor is bar:
                    break
                matches = [n for n in ancestor.walk() if n is not menu and
                           n.type in ("ButtonControl", "HyperlinkControl") and
                           (n.name == title or n.name.endswith(" " + title)) and
                           not n.name.startswith("More options for ")]
                if matches:
                    row = matches[0] if len(matches) == 1 else None
                    break
            if row:
                entries.append(dict(key=key_for(kind, title), title=title,
                                    kind=kind, node=row, source="sidebar"))
    return panes, entries


def question_in(pane):
    """Only the live question widget; never infer choices from chat prose."""
    nodes = pane.ui_nodes()
    for marker in nodes:
        if marker.type != "ButtonControl" or marker.name != "Dismiss question":
            continue
        group = marker.parent
        children = list(group.walk())
        submit = [n for n in children if n.type == "ButtonControl" and n.name.casefold() in SUBMIT]
        if len(submit) != 1:
            continue
        options = [n for n in group.children if n.type in
                   ("ButtonControl", "CheckBoxControl", "RadioButtonControl") and
                   n.name not in ("Dismiss question", "View question options", "Skip") and
                   n.name.casefold() not in SUBMIT]
        if len(options) < 2:
            continue
        recommended = []
        for option in options:
            # The first text child is the label; descriptions may mention other
            # recommendations and must not select a different option by accident.
            label = next((n.name for n in option.walk() if n.type == "TextControl"), option.name)
            if RECOMMENDED.search(label) and not NEGATIVE.search(label):
                recommended.append(option)
        other = [n for n in children if n.type == "EditControl" and
                 n.name.casefold() in {"other option", "other", "inna odpowiedź"}]
        identity = "\n".join(n.name for n in group.children)
        return dict(group=group, options=options, recommended=recommended,
                    other=other[0] if len(other) == 1 else None,
                    submit=submit[0], fingerprint=hashlib.sha256(identity.encode()).hexdigest())
    return None


def signals(pane, detect_banner):
    question = question_in(pane)
    question_nodes = set(question["group"].walk()) if question else set()
    ui = [n for n in pane.ui_nodes() if n not in question_nodes and not rename_title(n)
          and not n.name.startswith("More options for ") and n.type != "EditControl"
          and not any(a.type == "EditControl" or a.name == "Repository and pull request controls"
                      for a in n.ancestors())]
    texts = [n.name for n in ui if n.type in ("TextControl", "ButtonControl", "StatusBarControl")]
    banner, reset = detect_banner(texts)
    retries = [n for n in ui if n.type == "ButtonControl" and n.name.casefold() in RETRY and n.visible]
    errors = [n.name for n in ui if n.type in ("TextControl", "StatusBarControl") and API_ERROR.search(n.name)]
    # Code can render a terminal API failure at the end of the transcript.
    # Consider only the last text leaf, not old errors, quotes, code blocks or
    # user messages. An arbitrary occurrence of the phrase is never sufficient.
    chat_nodes = [n for n in pane.root.walk() if n.inside("chat messages")]
    leaves = [n for n in chat_nodes if n.type == "TextControl" and n.name.strip()
              and not any(a.name == "Message actions" for a in n.ancestors())]
    if leaves:
        last = leaves[-1]
        ancestors = list(last.ancestors())
        if (API_ERROR.search(last.name) and not any(a.type in ("CodeControl", "EditControl") or
                a.role in ("code", "blockquote") or
                a.name.startswith(("You said:", "Message ")) for a in ancestors)):
            errors.append(last.name)
    error = errors[-1] if errors else None
    return dict(limit=bool(banner), reset=reset, error=error,
                permanent=bool(error and PERMANENT_ERROR.search(error)),
                retry=retries[0] if len(retries) == 1 else None,
                busy=any(n.type == "ButtonControl" and n.name.casefold() in
                         {"stop", "stop response", "stop generating", "zatrzymaj"} for n in ui),
                question=question)


class ClaudeUI:
    def __init__(self, worker):
        self.worker = worker

    def snapshot(self):
        from claude_auto_continue import auto
        win = self.worker._get_window()
        if win is None:
            raise RuntimeError("Claude window is unavailable")
        self.worker._wake_accessibility(win)
        root = Node("window", "WindowControl", control=win)
        stack = [(-1, root)]
        started = time.monotonic()
        for i, (ctrl, depth) in enumerate(auto.WalkControl(win, includeTop=False, maxDepth=130)):
            if i > 30000 or time.monotonic() - started > 15:
                raise RuntimeError("Accessibility scan incomplete; no action taken")
            try:
                r = ctrl.BoundingRectangle
                node = Node(ctrl.Name or "", ctrl.ControlTypeName,
                            (r.left, r.top, r.right, r.bottom), ctrl, role=ctrl.AriaRole or "")
            except Exception:
                raise RuntimeError("Accessibility tree changed; retry on next scan")
            while stack[-1][0] >= depth:
                stack.pop()
            node.parent = stack[-1][1]
            node.parent.children.append(node)
            stack.append((depth, node))
        return root

    def click(self, node):
        from claude_auto_continue import user32
        if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
            raise RuntimeError("Pending command; action deferred")
        self.worker._focus_window(self.worker.hwnd)
        if user32.GetForegroundWindow() != self.worker.hwnd:
            raise RuntimeError("Claude is not foreground")
        if not node.visible or not node.control.IsEnabled or node.control.IsOffscreen:
            raise RuntimeError("Control is unavailable")
        node.control.Click(simulateMove=False)

    def resolve(self, key, navigate=True):
        root = self.snapshot()
        panes, entries = discover(root)
        matches = [p for p in panes if p.key == key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 or not navigate:
            return None
        kind = key.split(":", 1)[0]
        rows = [e for e in entries if e["key"] == key]
        if not rows:
            label = "Code" if kind == "code" else "Chat and Cowork"
            switches = [n for n in root.walk() if n.type == "ButtonControl" and
                        (n.name == label or n.name.startswith(label + ",")) and n.inside("sidebar")]
            if len(switches) == 1:
                self.click(switches[0])
                time.sleep(0.8)
                root = self.snapshot()
                panes, entries = discover(root)
                rows = [e for e in entries if e["key"] == key]
        if len(rows) != 1:
            return None
        self.click(rows[0]["node"])
        # Navigation is asynchronous. Verify the requested full title and its
        # own composer, never assume that a successful click opened the chat.
        for _ in range(4):
            time.sleep(0.4)
            panes, _ = discover(self.snapshot())
            matches = [p for p in panes if p.key == key]
            if len(matches) == 1:
                return matches[0]
        return None

    @staticmethod
    def value(node):
        try:
            value = node.control.GetValuePattern().Value.strip()
        except Exception:
            try:
                value = node.control.GetTextPattern().DocumentRange.GetText(-1).strip()
            except Exception:
                raise RuntimeError("Cannot verify composer contents")
        # Code includes its non-editable placeholder in both UIA text patterns.
        # It has a nested label plus a separate empty editable text leaf. A typed
        # draft with the same words has no separate placeholder group.
        leaves = [n for n in node.walk() if n.type == "TextControl"]
        if (node.name.casefold() == "prompt" and value in {"Type / for commands", "Type a message..."}
                and len(leaves) == 2 and not leaves[-1].name.strip()
                and leaves[0].parent is not node):
            return ""
        return value

    def type_into(self, node, text):
        from claude_auto_continue import auto, user32
        if self.value(node):
            raise RuntimeError("Existing draft; leaving it untouched")
        self.click(node)
        time.sleep(0.15)
        if user32.GetForegroundWindow() != self.worker.hwnd or not node.control.HasKeyboardFocus:
            raise RuntimeError("Composer focus could not be verified")
        # Prefer an element-scoped write; Chromium sometimes exposes a read-only
        # ValuePattern. Fallback chunks recheck focus and pending Stop commands.
        wrote_value = False
        try:
            pattern = node.control.GetValuePattern()
            if pattern and not pattern.IsReadOnly:
                pattern.SetValue(text)
                wrote_value = True
        except Exception:
            if self.value(node):
                raise RuntimeError("Text write outcome is uncertain; leaving the draft untouched")
        if not wrote_value:
            for start in range(0, len(text), 32):
                if (user32.GetForegroundWindow() != self.worker.hwnd or not node.control.HasKeyboardFocus
                        or self.worker._stop_event.is_set() or not self.worker.cmds.empty()):
                    raise RuntimeError("Input interrupted; message was not submitted")
                auto.SendKeys(self.worker._escape_sendkeys(text[start:start + 32]), interval=0.001, waitTime=0.01)
        if user32.GetForegroundWindow() != self.worker.hwnd or not node.control.HasKeyboardFocus:
            raise RuntimeError("Focus changed; message was not submitted")
        if self.value(node) != text:
            raise RuntimeError("Typed text could not be verified; message was not submitted")

    def resume(self, key, prefer_retry=False, api_error=False):
        from claude_auto_continue import auto, detect_limit_banner
        pane = self.resolve(key)
        if pane is None:
            raise RuntimeError("Selected conversation is missing or ambiguous")
        state = signals(pane, detect_limit_banner)
        if state["question"] or state["busy"]:
            return "busy"
        if self.value(pane.prompt):
            raise RuntimeError("Existing draft; leaving it untouched")
        if state["retry"] and (prefer_retry or api_error):
            self.click(state["retry"])
            return "retry"
        if api_error and not state["error"]:
            return "cleared"
        message = " ".join((self.worker.cfg.get("message") or "continue").split())
        self.type_into(pane.prompt, message)
        if self.worker._stop_event.is_set() or not self.worker.cmds.empty():
            raise RuntimeError("Pending command; message was not submitted")
        auto.SendKeys("{Enter}", waitTime=0.1)
        return "message"

    def answer(self, key, fingerprint):
        pane = self.resolve(key)
        q = question_in(pane) if pane else None
        if not q or q["fingerprint"] != fingerprint:
            return False
        # Respect a partially entered answer or selection made by the user.
        if q["other"] and self.value(q["other"]):
            raise RuntimeError("Question already has a draft answer")
        for option in q["options"]:
            selected = self.selected(option)
            if selected is None:
                raise RuntimeError("Cannot verify question selection state")
            if selected:
                raise RuntimeError("Question already has a selected answer")
        chosen = q["recommended"]
        if len(chosen) > 1 and any(n.type == "RadioButtonControl" for n in chosen):
            raise RuntimeError("Multiple recommendations in a single-choice question")
        if not chosen:
            if not q["other"]:
                raise RuntimeError("No recommended option or Other field")
            other_buttons = [n for n in q["options"] if n.name.casefold() in OTHER]
            if len(other_buttons) == 1:
                self.click(other_buttons[0])
                pane = self.resolve(key, navigate=False)
                q = question_in(pane) if pane else None
                if not q or q["fingerprint"] != fingerprint:
                    return False
            self.type_into(q["other"], "Pick your recommended option(s).")
        else:
            # Re-resolve between every selection: no stale coordinates when the
            # card grows, changes question, or the user rearranges a split.
            labels = [n.name for n in chosen]
            for label in labels:
                pane = self.resolve(key, navigate=False)
                current = question_in(pane) if pane else None
                if not current or current["fingerprint"] != fingerprint:
                    return False
                option = next((n for n in current["recommended"] if n.name == label), None)
                if not option:
                    return False
                self.click(option)
        pane = self.resolve(key, navigate=False)
        current = question_in(pane) if pane else None
        if not current or current["fingerprint"] != fingerprint:
            return False
        if chosen and not all(self.selected(n) for n in current["recommended"]):
            raise RuntimeError("Recommended selections could not be verified")
        self.click(current["submit"])
        return True

    @staticmethod
    def selected(node):
        """Read both multi-choice toggles and native single-choice controls."""
        for getter, prop in (("GetTogglePattern", "ToggleState"), ("GetSelectionItemPattern", "IsSelected")):
            method = getattr(node.control, getter, None)
            if method:
                pattern = method()
                if pattern is not None:
                    value = getattr(pattern, prop)
                    return value == 1 if prop == "ToggleState" else bool(value)
        return None


@dataclass
class SessionState:
    phase: str = "watching"
    reason: str = ""
    due: object = None
    reset: object = None
    attempts: int = 0
    question_id: str = ""
    notice: str = ""
    next_panel: float = 0


class SessionEngine:
    def __init__(self, worker, ui=None):
        self.worker = worker
        self.ui = ui or ClaudeUI(worker)
        self.sessions = {}
        self.catalog = {}
        self.cursor = 0
        self.next_scan = 0
        self.last_scan_error = ""

    def refresh(self):
        panes, entries = discover(self.ui.snapshot())
        previous = self.catalog
        self.catalog = {}
        # Follow Claude's current sidebar order instead of first-discovery order.
        # A newly opened chat may not have reached the sidebar yet: put it first.
        sidebar_keys = {entry["key"] for entry in entries}
        for pane in panes:
            if pane.key not in sidebar_keys:
                self.catalog[pane.key] = dict(key=pane.key, title=pane.title, kind=pane.kind,
                                              source="open", available=True)
        for entry in entries:
            self.catalog[entry["key"]] = {k: v for k, v in entry.items() if k != "node"}
            self.catalog[entry["key"]]["available"] = True
        for pane in panes:
            self.catalog[pane.key] = dict(key=pane.key, title=pane.title, kind=pane.kind,
                                          source="open", available=True)
        for key, item in previous.items():
            if key not in self.catalog:
                self.catalog[key] = dict(item, available=False)
        for key in self.worker.cfg.get("selected_chats", []):
            if key not in self.catalog and ":" in key:
                kind, title = key.split(":", 1)
                self.catalog[key] = dict(key=key, kind=kind, title=title, source="sidebar", available=False)
        self.publish()
        return panes

    def targets(self, panes):
        if self.worker.cfg.get("watch_scope", "open") == "selected":
            return list(dict.fromkeys(self.worker.cfg.get("selected_chats", [])))
        return list(dict.fromkeys(p.key for p in panes))

    def publish(self):
        rows = []
        for key, item in self.catalog.items():
            state = self.sessions.get(key, SessionState())
            selected = (key in self.worker.cfg.get("selected_chats", []) if self.worker.cfg.get("watch_scope") == "selected"
                        else item.get("source") == "open" and item.get("available"))
            phase = state.phase if selected and self.worker.state != "IDLE" else "inactive"
            rows.append(dict(item, phase=phase, due=state.due, attempts=state.attempts,
                             notice=state.notice))
        self.worker.emit("chats", rows)
        pending = [s for s in self.sessions.values() if s.phase in ("waiting", "verifying") and s.due]
        nearest = min(pending, key=lambda s: s.due) if pending else None
        self.worker.reset_at = nearest.reset if nearest else None
        self.worker.send_at = nearest.due if nearest else None
        self.worker.emit("state", dict(state="IDLE" if self.worker.state == "IDLE" else "ARMED" if nearest else "MONITORING",
                                       reset_at=self.worker.reset_at, send_at=self.worker.send_at))

    def reset(self):
        self.sessions.clear()
        self.next_scan = 0

    def note(self, key, state, text):
        if state.notice != text:
            state.notice = text
            self.worker.log("log_chat_action", chat=key.split(":", 1)[-1], action=self.worker.t(text))

    def arm(self, reset):
        panes = self.refresh()
        for key in self.targets(panes):
            self.sessions[key] = SessionState(phase="waiting", reason="limit", reset=reset,
                due=reset + dt.timedelta(seconds=self.worker.cfg["send_delay_after_reset_s"]))
        self.publish()

    def tick(self, now=None):
        from claude_auto_continue import detect_limit_banner
        now = now or dt.datetime.now()
        if time.monotonic() < self.next_scan:
            return
        try:
            panes = self.refresh()
            self.last_scan_error = ""
        except Exception as exc:
            self.next_scan = time.monotonic() + self.worker.cfg["scan_interval_s"]
            self.worker.emit("status", "no_window")
            if str(exc) != self.last_scan_error:
                self.last_scan_error = str(exc)
                self.worker.log("log_monitor_error", "warn", err=str(exc))
            return
        targets = self.targets(panes)
        self.sessions = {k: v for k, v in self.sessions.items() if k in targets}
        self.next_scan = time.monotonic() + max(2, self.worker.cfg["scan_interval_s"] / max(1, len(targets)))
        if not targets:
            self.publish()
            return
        key = targets[self.cursor % len(targets)]
        self.cursor += 1
        state = self.sessions.setdefault(key, SessionState())
        try:
            pane = self.ui.resolve(key, navigate=self.worker.cfg.get("watch_scope") == "selected")
            if pane is None:
                self.note(key, state, "unavailable")
                return
            observed = signals(pane, detect_limit_banner)
            self.worker.emit("status", "ok")
            # Retain the original 5-hour meter fallback, scoped to this pane.
            # Never use a weekly/context percentage as a session-limit trigger.
            meter = next((n for n in pane.ui_nodes() if n.type == "ButtonControl" and
                          n.name.startswith("Usage:")), None)
            percentages = [int(p) for p in re.findall(r"(\d+)%", meter.name)] if meter else []
            need_panel = ((observed["limit"] and not observed["reset"]) or
                          (not observed["limit"] and percentages and max(percentages) >= 100))
            if need_panel and time.monotonic() >= state.next_panel and state.phase == "watching":
                state.next_panel = time.monotonic() + self.worker.cfg.get("panel_backoff_s", 300)
                rows, ok = self.worker._read_usage_panel(self.worker._get_window(), meter_scope=pane.root.control)
                if ok:
                    self.worker.emit("usage", {"rows": rows, "session": pane.title})
                    h5 = rows.get("5h", {})
                    if observed["limit"] or h5.get("pct", 0) >= self.worker.cfg.get("limit_threshold_pct", 100):
                        observed["limit"] = True
                        observed["reset"] = observed["reset"] or h5.get("reset")
            self.step(key, state, observed, now)
        except Exception as exc:
            self.note(key, state, str(exc))
        finally:
            self.publish()

    def step(self, key, state, observed, now):
        cfg = self.worker.cfg
        if state.phase == "exhausted":
            return
        if observed["permanent"] and cfg.get("retry_api_errors"):
            state.phase, state.due = "exhausted", None
            self.note(key, state, "permanent_error")
            return
        if observed["question"]:
            state.phase, state.due = "question", None
            qid = observed["question"]["fingerprint"]
            if cfg.get("auto_approach") and cfg["auto_send"] and qid != state.question_id:
                # Mark before side effects: an uncertain Submit must not be repeated.
                state.question_id = qid
                if self.ui.answer(key, qid):
                    self.note(key, state, "approach_submitted")
            return
        if state.phase == "question":
            state.phase = "watching"
        state.question_id = ""
        if observed["limit"] and state.reason != "limit":
            # A rate-limit notice supersedes a previously scheduled short API
            # retry, even if that retry became due during this scan.
            state.phase = "watching"
        if state.phase == "verifying" and state.due and now < state.due:
            return
        if state.phase == "verifying":
            if not observed["limit"] and not observed["error"] and not observed["retry"]:
                state.phase, state.reason, state.due = "watching", "", None
                state.attempts = 0
                self.note(key, state, "resumed")
                return
            state.phase = "watching"
        if state.phase == "watching":
            if observed["limit"]:
                state.reason, state.reset = "limit", observed["reset"]
                state.due = (state.reset + dt.timedelta(seconds=cfg["send_delay_after_reset_s"]) if state.reset
                             else now + dt.timedelta(seconds=cfg["retry_wait_s"]))
            elif cfg.get("retry_api_errors") and (observed["error"] or observed["retry"]):
                state.reason = "api"
                state.due = now + dt.timedelta(seconds=min(900, cfg.get("api_retry_wait_s", 30) * 2 ** state.attempts))
            else:
                return
            state.phase = "waiting"
            self.note(key, state, "waiting_" + state.reason)
        if state.phase != "waiting" or now < state.due:
            return
        if state.reason == "api" and not cfg.get("retry_api_errors"):
            state.phase, state.due = "watching", None
            return
        if not cfg["auto_send"]:
            state.phase = "exhausted"
            self.worker.emit("beep")
            self.note(key, state, "alert_only")
            return
        if state.attempts >= cfg["max_retries"]:
            state.phase = "exhausted"
            self.note(key, state, "retry_limit")
            return
        # If the user already resumed the chat, don't inject another message.
        if observed["busy"]:
            state.phase, state.due = "watching", None
            return
        if state.reason == "api" and not (observed["error"] or observed["retry"]):
            state.phase, state.due = "watching", None
            return
        state.attempts += 1
        state.phase = "verifying"
        state.due = now + dt.timedelta(seconds=cfg.get("verify_delay_s", 30))
        try:
            result = self.ui.resume(key, cfg.get("prefer_try_again", False), state.reason == "api")
        except Exception:
            state.phase = "waiting"
            state.due = now + dt.timedelta(seconds=max(30, cfg.get("api_retry_wait_s", 30)))
            raise
        self.note(key, state, result)
