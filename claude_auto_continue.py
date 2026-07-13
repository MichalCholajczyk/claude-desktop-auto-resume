# -*- coding: utf-8 -*-
"""
Claude Auto-Resume — auto-continue for Claude Desktop (Windows 11).

Watches the Claude app window via UI Automation, detects the usage-limit
message (5-hour / weekly), parses the reset time and, one minute after the
reset, types "continue" into the chat box and presses Enter.

UI is bilingual (English / Polish), default English.

Requires: Python 3.10+, package `uiautomation` (pip install uiautomation).
"""

import ctypes
import ctypes.wintypes
import datetime as dt
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

try:
    import uiautomation as auto
except ImportError:
    ctypes.windll.user32.MessageBoxW(
        0,
        "Missing package 'uiautomation'.\n\nInstall it:  py -m pip install uiautomation",
        "Claude Auto-Resume", 0x10)
    sys.exit(1)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "auto_continue_config.json")
LOG_PATH = os.path.join(APP_DIR, "auto_continue.log")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------------------------------------------------------- configuration

DEFAULT_CONFIG = {
    "language": "en",               # "en" or "pl"
    "scan_interval_s": 20,          # how often to scan the window (seconds)
    "send_delay_after_reset_s": 60, # seconds after reset to send "continue"
    "message": "continue",          # what to type into the chat
    "auto_send": True,              # False = only alert, do not send
    "keep_awake": True,             # keep Windows from sleeping
    "max_retries": 6,               # retries while the limit is still active
    "retry_wait_s": 600,            # retry spacing when reset time is unknown
    "limit_threshold_pct": 100,     # 5-hour limit % that counts as "hit"
    "panel_backoff_s": 300,         # min spacing between usage-panel opens while
                                    # the plan meter is maxed out by a weekly limit
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    if cfg.get("language") not in ("en", "pl"):
        cfg["language"] = "en"
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

# ----------------------------------------------------------------- translations

STRINGS = {
    "en": {
        # window states
        "state_idle": "Watch is off",
        "state_monitoring": "Watching your session",
        "state_armed": "Limit hit — waiting for reset",
        "state_verify": "Sent — checking result",
        # header captions
        "cap_click_start": "Click “Start watching”.",
        "cap_no_window": "Can’t see the Claude window — open the app and click “Refresh”.",
        "cap_guarding": "Guarding session: {session}",
        "cap_scanning": "Scanning the Claude window every {sec} s.",
        "cap_armed_reset": "Reset {reset} — I’ll send “continue” at {send}.",
        "cap_armed_noreset": "Reset time unknown — I’ll try at {send}.",
        "cap_verify": "About to check whether the session resumed.",
        # usage row
        "usage_5h_none": "5-hour limit —",
        "usage_5h": "5-hour limit {pct}%",
        "usage_weekly": "weekly {pct}%",
        "usage_fable": "Fable {pct}%",
        "usage_5h_reset": "resets {reset}",
        # section headers
        "sec_control": "CONTROL",
        "sec_log": "LOG",
        # controls
        "lbl_window": "Claude window:",
        "btn_refresh": "Refresh",
        "btn_start": "Start watching",
        "btn_stop": "Stop",
        "btn_send_now": "Send “continue” now",
        "lbl_scan_every": "Scan every",
        "lbl_seconds": "s",
        "chk_autosend": "Send automatically",
        "chk_awake": "Keep the PC awake",
        "lbl_message": "Message to send:",
        "lbl_know_reset": "Know the reset time?",
        "btn_arm": "Arm",
        "lbl_arm_hint": "format HH:MM — I’ll send a minute after that time",
        "combo_handle": "{title}  (handle {hwnd})",
        # dialogs
        "dlg_send_title": "Confirm send",
        "dlg_send_body": "Type “continue” and press Enter in the Claude window now?",
        "dlg_time_title": "Invalid time",
        "dlg_time_format": "Enter the reset time as HH:MM, e.g. 15:00.",
        "dlg_time_range": "Time must be between 00:00 and 23:59.",
        # log lines
        "log_monitor_error": "Monitor ERROR: {err}",
        "log_no_window_start": "Claude window not found — open Claude and click Refresh.",
        "log_started": "Watching started.",
        "log_stopped": "Watching stopped.",
        "log_manual_send": "Manual “continue” send…",
        "log_armed_manual": "Armed manually: reset {reset}, send at {send}.",
        "log_autosend_off": "Send time passed, but auto-send is OFF — alert only.",
        "log_window_refound": "Claude window found again (handle {hwnd}).",
        "log_5h_hit": "5-HOUR LIMIT HIT ({pct}%). Resets {reset}. I’ll send at {send}.",
        "log_5h_reset_updated": "5-hour reset updated: {reset}.",
        "log_5h_cleared": "5-hour limit is clear ({pct}%) — nothing to send, back to watching.",
        "log_panel_unreadable": "Couldn’t read the usage panel — will try again.",
        "log_send_fail_nowin": "SEND FAILED: no Claude window.",
        "log_send_abort_fg": "SEND ABORTED: Claude window isn’t in front (won’t type into another app).",
        "log_sent": "Sent “{message}” + Enter.",
        "log_send_fail": "SEND FAILED: {err}",
        "log_retries_done": "Out of retries — back to normal watching.",
        "log_retry_at": "Retry at {send} ({n}/{max}).",
        "log_still_done": "Limit still active, out of retries — keep watching.",
        "log_still_new": "Limit still active — new reset {reset}, send at {send}.",
        "log_still_retry": "Limit still active — retry at {send} ({n}/{max}).",
        "log_success": "SUCCESS — limit cleared, session resumed. Watching on.",
    },
    "pl": {
        "state_idle": "Czuwanie wyłączone",
        "state_monitoring": "Czuwam nad sesją",
        "state_armed": "Limit strzelony — czekam na reset",
        "state_verify": "Wysłano — sprawdzam efekt",
        "cap_click_start": "Kliknij „Rozpocznij czuwanie”.",
        "cap_no_window": "Nie widzę okna Claude — uruchom aplikację i kliknij „Odśwież”.",
        "cap_guarding": "Pilnuję sesji: {session}",
        "cap_scanning": "Skanuję okno Claude co {sec} s.",
        "cap_armed_reset": "Reset {reset} — wyślę „continue” o {send}.",
        "cap_armed_noreset": "Nie znam godziny resetu — spróbuję o {send}.",
        "cap_verify": "Za chwilę sprawdzę, czy sesja ruszyła.",
        "usage_5h_none": "limit 5-godzinny —",
        "usage_5h": "limit 5-godzinny {pct}%",
        "usage_weekly": "tygodniowy {pct}%",
        "usage_fable": "Fable {pct}%",
        "usage_5h_reset": "reset {reset}",
        "sec_control": "STEROWANIE",
        "sec_log": "DZIENNIK",
        "lbl_window": "Okno Claude:",
        "btn_refresh": "Odśwież",
        "btn_start": "Rozpocznij czuwanie",
        "btn_stop": "Zatrzymaj",
        "btn_send_now": "Wyślij „continue” teraz",
        "lbl_scan_every": "Skanuj co",
        "lbl_seconds": "s",
        "chk_autosend": "Wysyłaj automatycznie",
        "chk_awake": "Nie usypiaj komputera",
        "lbl_message": "Wysyłany tekst:",
        "lbl_know_reset": "Znasz godzinę resetu?",
        "btn_arm": "Uzbrój",
        "lbl_arm_hint": "format HH:MM — wyślę minutę po tej godzinie",
        "combo_handle": "{title}  (uchwyt {hwnd})",
        "dlg_send_title": "Potwierdź wysyłkę",
        "dlg_send_body": "Wpisać „continue” i wcisnąć Enter w oknie Claude teraz?",
        "dlg_time_title": "Nieprawidłowa godzina",
        "dlg_time_format": "Wpisz godzinę resetu w formacie HH:MM, np. 15:00.",
        "dlg_time_range": "Godzina musi być z zakresu 00:00–23:59.",
        "log_monitor_error": "BŁĄD monitora: {err}",
        "log_no_window_start": "Nie znaleziono okna Claude — uruchom Claude i kliknij Odśwież.",
        "log_started": "Czuwanie uruchomione.",
        "log_stopped": "Czuwanie zatrzymane.",
        "log_manual_send": "Ręczna wysyłka „continue”…",
        "log_armed_manual": "Uzbrojono ręcznie: reset {reset}, wysyłka {send}.",
        "log_autosend_off": "Czas wysyłki minął, ale auto-wysyłka jest WYŁĄCZONA — tylko alarm.",
        "log_window_refound": "Okno Claude odnalezione ponownie (uchwyt {hwnd}).",
        "log_5h_hit": "LIMIT 5-GODZINNY STRZELONY ({pct}%). Reset {reset}. Wyślę o {send}.",
        "log_5h_reset_updated": "Zaktualizowano reset 5h: {reset}.",
        "log_5h_cleared": "Limit 5-godzinny wolny ({pct}%) — nie ma czego wysyłać, wracam do czuwania.",
        "log_panel_unreadable": "Nie udało się odczytać panelu zużycia — spróbuję ponownie.",
        "log_send_fail_nowin": "WYSYŁKA NIEUDANA: brak okna Claude.",
        "log_send_abort_fg": "WYSYŁKA PRZERWANA: okno Claude nie jest na wierzchu (nie będę pisać do innej aplikacji).",
        "log_sent": "Wysłano „{message}” + Enter.",
        "log_send_fail": "WYSYŁKA NIEUDANA: {err}",
        "log_retries_done": "Wyczerpano próby — wracam do zwykłego czuwania.",
        "log_retry_at": "Ponowna próba o {send} ({n}/{max}).",
        "log_still_done": "Limit nadal aktywny, wyczerpano próby — czuwam dalej.",
        "log_still_new": "Limit nadal aktywny — nowy reset {reset}, wysyłka o {send}.",
        "log_still_retry": "Limit nadal aktywny — ponowię o {send} ({n}/{max}).",
        "log_success": "SUKCES — limit zniknął, sesja wznowiona. Czuwam dalej.",
    },
}


def tr(lang, key, **kw):
    """Translate a key; fall back to English, then to the raw key."""
    template = STRINGS.get(lang, STRINGS["en"]).get(key)
    if template is None:
        template = STRINGS["en"].get(key, key)
    try:
        return template.format(**kw)
    except (KeyError, IndexError, ValueError):
        return template

# ------------------------------------------------------------------- detection

# Phrases marking a HARD limit (session blocked) — not a mere warning.
HARD_LIMIT_PATTERNS = [
    r"you'?ve\s+reached\s+your",
    r"limit\s+reached",
    r"reached\s+(?:your\s+|the\s+)?(?:usage|session|5-hour|weekly)\s*limit",
    r"out\s+of\s+usage",
    r"you'?re\s+out\s+of",
    r"hit\s+(?:your|the)\s+.{0,30}?limit",
    r"used\s+100\s*%",
    r"100\s*%\s+of\s+your",
    r"5-hour\s+limit",
    r"plan\s+100\s*%",
    r"limit\s+(?:zosta[łl]\s+)?osi[ąa]gni[ęe]ty",
    r"osi[ąa]gn[ąą]?[łl](?:e[śs])?\s+.{0,30}?limit",
]

# e.g. "Resets Mon, Jul 13, 6:00 PM" / "resets 3pm" / "resets at 6:30 PM"
# (also matches Claude's Polish UI wording, e.g. the "resetuje ... o 15:00" form)
RE_RESET_ABS = re.compile(
    r"reset(?:s|uje(?:\s*si[eę])?)?\s*(?:at\s+|o\s+|:\s*)?"
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun|pon|wt|śr|czw|pt|sob|ndz?|nie)[a-ząćęłńóśźż]*\.?,?\s+)?"
    r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|sty|lut|kwi|maj|cze|lip|sie|wrz|paź|lis|gru)"
    r"[a-ząćęłńóśźż]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
    re.IGNORECASE)

# relative form, e.g. "Resets in 2 hr 15 min" (English or Claude's Polish "za ... min")
RE_RESET_REL = re.compile(
    r"reset\w*\s+(?:in|za)\s+"
    r"(?:(\d+)\s*(?:hours?|hrs?|h|godz\w*)\.?)?\s*,?\s*"
    r"(?:(\d+)\s*(?:minut\w*|min(?:ute)?s?|m)\.?)?",
    re.IGNORECASE)

RE_USED_PCT = re.compile(r"used\s+(\d{1,3})\s*%", re.IGNORECASE)
RE_PLAN_PCT = re.compile(r"plan\s+(\d{1,3})\s*%", re.IGNORECASE)

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
          "sty": 1, "lut": 2, "kwi": 4, "maj": 5, "cze": 6,
          "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "lis": 11, "gru": 12}

# UI groups whose contents we ignore (so chatting *about* limits won't false-trigger)
EXCLUDED_GROUPS = {"chat messages", "sidebar", "recents"}


def parse_reset_time(text, now=None):
    """Return the reset datetime extracted from text, or None."""
    now = now or dt.datetime.now()

    m = RE_RESET_ABS.search(text)
    if m:
        mon, day, hh, mm, ampm = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # reject matches without minutes/AM-PM/date (e.g. a stray "resets 5")
        if mm is not None or ampm or mon:
            h = int(hh)
            minute = int(mm) if mm is not None else 0
            if ampm:
                ampm = ampm.lower()
                if ampm == "pm" and h < 12:
                    h += 12
                elif ampm == "am" and h == 12:
                    h = 0
            if 0 <= h <= 23 and 0 <= minute <= 59:
                if mon:
                    month = MONTHS[mon.lower()[:3]]
                    year = now.year
                    try:
                        t = dt.datetime(year, month, int(day), h, minute)
                    except ValueError:
                        t = None
                    if t:
                        if t < now - dt.timedelta(hours=12):
                            t = t.replace(year=year + 1)
                        return t
                else:
                    t = now.replace(hour=h, minute=minute, second=0, microsecond=0)
                    if t <= now:
                        t += dt.timedelta(days=1)
                    return t

    m = RE_RESET_REL.search(text)
    if m and (m.group(1) or m.group(2)):
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        if hours or minutes:
            return now + dt.timedelta(hours=hours, minutes=minutes)
    return None


def find_hard_limit(text, extra_patterns=()):
    """Return the matched hard-limit phrase, or None."""
    for pat in list(HARD_LIMIT_PATTERNS) + list(extra_patterns):
        try:
            m = re.search(pat, text, re.IGNORECASE)
        except re.error:
            continue
        if m:
            return m.group(0)
    return None

# -------------------------------------------------------------- Windows layer

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake(armed):
    """Keep the system awake; when armed, keep the display on too."""
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if armed:
        flags |= ES_DISPLAY_REQUIRED
    kernel32.SetThreadExecutionState(flags)


def allow_sleep():
    kernel32.SetThreadExecutionState(ES_CONTINUOUS)


def process_exe_name(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)

# --------------------------------------------------------------- monitor thread


class MonitorWorker(threading.Thread):
    """All UI Automation calls happen in this thread (its own COM apartment)."""

    IDLE, MONITORING, ARMED, VERIFY = "IDLE", "MONITORING", "ARMED", "VERIFY"

    def __init__(self, out_queue, cfg):
        super().__init__(daemon=True)
        self.out = out_queue          # (kind, data) -> UI
        self.cmds = queue.Queue()     # commands from UI
        self.cfg = cfg
        self.state = self.IDLE
        self.hwnd = None
        self.reset_at = None
        self.send_at = None
        self.verify_at = None
        self.retries = 0
        self.next_scan = 0.0
        self.next_panel_read = 0.0    # throttle for opening the usage popover
        self.panel_fail = 0
        self.last_rows = {}           # last usage-panel reading
        self._stop = threading.Event()

    # --- API for the UI thread (thread-safe) ---
    def command(self, name, payload=None):
        self.cmds.put((name, payload))

    def shutdown(self):
        self._stop.set()

    # --- messages to the UI ---
    def emit(self, kind, data=None):
        self.out.put((kind, data))

    def t(self, key, **kw):
        return tr(self.cfg.get("language", "en"), key, **kw)

    def log(self, key, level="info", **kw):
        msg = self.t(key, **kw)
        line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        self.emit("log", (line, level))
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- main loop
    def run(self):
        with auto.UIAutomationInitializerInThread():
            auto.SetGlobalSearchTimeout(3)
            while not self._stop.is_set():
                try:
                    self._process_commands()
                    self._tick()
                except Exception as e:  # the monitor must not die silently
                    self.log("log_monitor_error", "bad", err=repr(e))
                    time.sleep(2)
                time.sleep(0.5)
        allow_sleep()

    def _process_commands(self):
        while True:
            try:
                name, payload = self.cmds.get_nowait()
            except queue.Empty:
                return
            if name == "refresh_windows":
                self.emit("windows", self._enum_windows())
            elif name == "select_window":
                self.hwnd = payload
            elif name == "start":
                if not self.hwnd:
                    wins = self._enum_windows()
                    if wins:
                        self.hwnd = wins[0][0]
                        self.emit("windows", wins)
                if not self.hwnd:
                    self.log("log_no_window_start", "warn")
                    continue
                self.state = self.MONITORING
                self.retries = 0
                self.next_scan = 0.0
                if self.cfg["keep_awake"]:
                    keep_awake(False)
                self.log("log_started")
                self.emit("state", self._state_info())
            elif name == "stop":
                self.state = self.IDLE
                self.reset_at = self.send_at = None
                allow_sleep()
                self.log("log_stopped")
                self.emit("state", self._state_info())
            elif name == "send_now":
                self.log("log_manual_send")
                self._do_send(manual=True)
            elif name == "arm_manual":
                self.reset_at = payload
                self.send_at = payload + dt.timedelta(seconds=self.cfg["send_delay_after_reset_s"])
                self.state = self.ARMED
                self.retries = 0
                if self.cfg["keep_awake"]:
                    keep_awake(True)
                self.log("log_armed_manual",
                         reset=f"{payload:%Y-%m-%d %H:%M}",
                         send=f"{self.send_at:%H:%M:%S}")
                self.emit("state", self._state_info())
            elif name == "config":
                self.cfg.update(payload)

    def _tick(self):
        now = time.time()
        if self.state == self.MONITORING:
            if now >= self.next_scan:
                self.next_scan = now + self.cfg["scan_interval_s"]
                self._scan_and_decide()
        elif self.state == self.ARMED:
            # We already know the 5-hour reset time, so just wait for it — no
            # need to keep opening the usage popover (it would flash all night).
            if dt.datetime.now() >= self.send_at:
                if self.cfg["auto_send"]:
                    self._do_send()
                else:
                    self.log("log_autosend_off", "warn")
                    self.emit("beep", None)
                    self.state = self.MONITORING
                    self.emit("state", self._state_info())
            self.emit("countdown", self._state_info())
        elif self.state == self.VERIFY:
            if dt.datetime.now() >= self.verify_at:
                self._verify_after_send()

    # ---------------------------------------------------------------- scanning
    def _get_window(self):
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            wins = self._enum_windows()
            if wins:
                self.hwnd = wins[0][0]
                self.emit("windows", wins)
                self.log("log_window_refound", hwnd=self.hwnd)
            else:
                return None
        try:
            return auto.ControlFromHandle(self.hwnd)
        except Exception:
            return None

    def _enum_windows(self):
        """List of (hwnd, title) for claude.exe windows."""
        result = []
        try:
            for w in auto.GetRootControl().GetChildren():
                try:
                    if w.ClassName != "Chrome_WidgetWin_1":
                        continue
                    name = w.Name or ""
                    exe = process_exe_name(w.ProcessId)
                    if exe == "claude.exe" or (not exe and "claude" in name.lower()):
                        result.append((w.NativeWindowHandle, name or "Claude"))
                except Exception:
                    continue
        except Exception:
            pass
        return result

    @staticmethod
    def _wake_accessibility(win):
        """Chromium builds the accessibility tree only when a client asks for it."""
        try:
            for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=80):
                if ctrl.ControlTypeName == "DocumentControl":
                    try:
                        tp = ctrl.GetTextPattern()
                        if tp:
                            tp.DocumentRange.GetText(32)
                    except Exception:
                        pass
                    try:
                        ctrl.GetChildren()
                    except Exception:
                        pass
        except Exception:
            pass

    def _collect(self, win, budget_s=30.0):
        """Return (texts_outside_chat, prompt_control, session_title)."""
        self._wake_accessibility(win)
        time.sleep(0.7)

        texts = []
        prompt = None
        session_title = None
        exclude_depth = None
        t0 = time.time()
        count = 0
        try:
            win_rect = win.BoundingRectangle
        except Exception:
            win_rect = None

        for ctrl, depth in auto.WalkControl(win, includeTop=False, maxDepth=150):
            count += 1
            if count > 30000 or time.time() - t0 > budget_s:
                break
            try:
                name = ctrl.Name or ""
                ct = ctrl.ControlTypeName
            except Exception:
                continue

            if exclude_depth is not None:
                if depth > exclude_depth:
                    continue        # inside an excluded group (chat/sidebar)
                exclude_depth = None
            if ct == "GroupControl" and name.strip().lower() in EXCLUDED_GROUPS:
                exclude_depth = depth
                continue

            if name.strip():
                texts.append(name)

            if prompt is None and name.strip().lower() == "prompt":
                prompt = ctrl
            if prompt is None and ct == "EditControl" and win_rect:
                try:
                    r = ctrl.BoundingRectangle
                    if r.bottom > win_rect.bottom - int(0.35 * win_rect.height()):
                        prompt = ctrl
                except Exception:
                    pass

            # best guess at the active session title: text in the top bar
            if session_title is None and win_rect and ct in ("ButtonControl", "TextControl"):
                try:
                    r = ctrl.BoundingRectangle
                    if 30 < r.top < 90 and (r.right - r.left) > 120 and name.strip() and \
                            name.strip().lower() not in ("search", "menu", "back", "forward"):
                        session_title = name.strip()
                except Exception:
                    pass

        return texts, prompt, session_title

    # -------------------------------------------------- usage panel (5h limit)
    @staticmethod
    def _find_usage_button(win):
        """The small round meter in the bottom bar; Name is 'Usage: ...'."""
        for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=150):
            try:
                if ctrl.ControlTypeName == "ButtonControl" and \
                        (ctrl.Name or "").strip().lower().startswith("usage"):
                    return ctrl
            except Exception:
                pass
        return None

    @staticmethod
    def _find_usage_panel(win):
        """The 'Usage' popover that appears after clicking the meter."""
        for ctrl, _ in auto.WalkControl(win, includeTop=False, maxDepth=150):
            try:
                if (ctrl.Name or "").strip() == "Usage" and \
                        ctrl.ControlTypeName in ("WindowControl", "GroupControl"):
                    return ctrl
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_usage_rows(panel):
        """Parse the flat 'label / Resets in X / N% / bar' list into per-limit
        dicts. Returns {'5h': {pct, reset}, 'weekly': {...}, 'weekly_fable': {...}}."""
        texts = []
        try:
            for ctrl, _ in auto.WalkControl(panel, includeTop=True, maxDepth=40):
                try:
                    n = (ctrl.Name or "").strip()
                except Exception:
                    continue
                if n:
                    texts.append(n)
        except Exception:
            return {}
        rows, cur = {}, None
        for tx in texts:
            low = tx.lower()
            if "5-hour" in low:
                cur = "5h"; rows.setdefault(cur, {})
            elif low.startswith("weekly") and "fable" in low:
                cur = "weekly_fable"; rows.setdefault(cur, {})
            elif low.startswith("weekly"):
                cur = "weekly"; rows.setdefault(cur, {})
            elif low.startswith(("context", "plan usage", "view usage", "usage")):
                cur = None
            elif cur:
                mp = re.fullmatch(r"(\d{1,3})\s*%", tx)
                if mp and "pct" not in rows[cur]:
                    rows[cur]["pct"] = int(mp.group(1))
                elif "reset" in low and "reset" not in rows[cur]:
                    rt = parse_reset_time(tx)
                    if rt:
                        rows[cur]["reset"] = rt
        return rows

    def _read_usage_panel(self, win):
        """Open the usage popover, read the per-limit rows, close it and restore
        the mouse cursor. Returns (rows, ok)."""
        btn = self._find_usage_button(win)
        if not btn:
            return {}, False
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        m0 = (pt.x, pt.y)
        try:
            btn.Click(simulateMove=False)
        except Exception:
            return {}, False
        time.sleep(1.0)
        rows, ok = {}, False
        try:
            self._wake_accessibility(win)
            panel = self._find_usage_panel(win)
            if panel:
                rows = self._parse_usage_rows(panel)
                ok = "5h" in rows
        except Exception:
            ok = False
        try:
            auto.SendKeys("{Esc}", waitTime=0.05)
        except Exception:
            pass
        time.sleep(0.35)
        try:
            user32.SetCursorPos(m0[0], m0[1])
        except Exception:
            pass
        return rows, ok

    def _scan_and_decide(self, refresh_only=False):
        win = self._get_window()
        if not win:
            self.emit("status", "no_window")
            return
        texts, _prompt, session = self._collect(win)
        joined = "  ".join(texts)

        # Cheap upper bound: the meter's "plan Y%" is the MAX across all limits,
        # so the 5-hour limit can only be maxed if this is maxed too. Only then
        # do we open the popover to check the 5-hour limit specifically.
        m = RE_PLAN_PCT.search(joined)
        plan_pct = int(m.group(1)) if m else None
        threshold = self.cfg.get("limit_threshold_pct", 100)

        rows = {}
        now = time.time()
        need_panel = (plan_pct is None or plan_pct >= threshold)
        if need_panel and now >= self.next_panel_read:
            rows, ok = self._read_usage_panel(win)
            if ok:
                self.panel_fail = 0
                self.last_rows = rows
            else:
                self.panel_fail += 1
                if self.panel_fail in (1, 5, 20):
                    self.log("log_panel_unreadable", "warn")

        info = {}
        if plan_pct is not None:
            info["plan"] = plan_pct
        if self.last_rows:
            info["rows"] = self.last_rows
        if session:
            info["session"] = session
        self.emit("usage", info)

        h5 = (rows or self.last_rows).get("5h", {}) if (rows or self.last_rows) else {}
        pct = h5.get("pct")
        reset = h5.get("reset")

        # Definitely not 5-hour-limited: plan meter below threshold, or panel
        # says the 5-hour limit is below threshold.
        limited = False
        if plan_pct is not None and plan_pct < threshold:
            limited = False
        elif pct is not None:
            limited = pct >= threshold
        else:
            # couldn't read the 5-hour figure and plan is maxed -> back off,
            # stay cautious (do not arm on an unknown).
            self.next_panel_read = now + self.cfg.get("panel_backoff_s", 300)
            if self.state == self.MONITORING:
                self.emit("status", "ok")
            return

        if not limited:
            # while plan stays maxed by a weekly limit, stop re-opening for a while
            if plan_pct is not None and plan_pct >= threshold:
                self.next_panel_read = now + self.cfg.get("panel_backoff_s", 300)
            if self.state == self.MONITORING:
                self.emit("status", "ok")
            return

        # 5-hour limit is genuinely hit
        if reset:
            new_send = reset + dt.timedelta(seconds=self.cfg["send_delay_after_reset_s"])
        else:
            new_send = dt.datetime.now() + dt.timedelta(seconds=self.cfg["retry_wait_s"])
        if self.state != self.ARMED or self.send_at is None or \
                abs((new_send - self.send_at).total_seconds()) > 90:
            self.reset_at = reset
            self.send_at = new_send
            if self.state != self.ARMED:
                self.log("log_5h_hit", "warn", pct=pct,
                         reset=(f"{reset:%a %H:%M}" if reset else "?"),
                         send=f"{new_send:%H:%M:%S}")
                self.emit("beep", None)
            elif reset:
                self.log("log_5h_reset_updated", reset=f"{reset:%a %H:%M}")
            self.state = self.ARMED
            if self.cfg["keep_awake"]:
                keep_awake(True)
            self.emit("state", self._state_info())

    # ------------------------------------------------------------------ sending
    @staticmethod
    def _escape_sendkeys(s):
        """uiautomation treats { and } as key-sequence delimiters; escape them
        so an arbitrary user message is typed literally."""
        out = []
        for c in s:
            if c == "{":
                out.append("{{}")
            elif c == "}":
                out.append("{}}")
            else:
                out.append(c)
        return "".join(out)

    def _focus_window(self, hwnd):
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.6)
        user32.SwitchToThisWindow(hwnd, True)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.5)

    def _do_send(self, manual=False):
        win = self._get_window()
        if not win:
            self.log("log_send_fail_nowin", "bad")
            self._after_send_failed()
            return

        # Automatic send: confirm the 5-hour limit is really still hit before
        # typing, so we never spam a session that already cleared.
        if not manual:
            threshold = self.cfg.get("limit_threshold_pct", 100)
            rows, ok = self._read_usage_panel(win)
            if ok:
                self.last_rows = rows
                pct = rows.get("5h", {}).get("pct")
                if pct is not None and pct < threshold:
                    self.log("log_5h_cleared", "good", pct=pct)
                    self.emit("usage", {"rows": rows})
                    self.retries = 0
                    self.reset_at = self.send_at = None
                    self.state = self.MONITORING
                    if self.cfg["keep_awake"]:
                        keep_awake(False)
                    self.emit("state", self._state_info())
                    return

        hwnd = self.hwnd
        try:
            self._focus_window(hwnd)
            texts, prompt, _ = self._collect(win, budget_s=15.0)

            if prompt is not None:
                try:
                    prompt.Click(simulateMove=False)
                except Exception:
                    prompt = None
            if prompt is None:
                # fallback: click just above the bottom button bar, window center
                r = win.BoundingRectangle
                auto.Click(int((r.left + r.right) / 2), int(r.bottom - 80))
            time.sleep(0.6)

            if user32.GetForegroundWindow() != hwnd:
                self._focus_window(hwnd)
                time.sleep(0.4)
            if user32.GetForegroundWindow() != hwnd:
                self.log("log_send_abort_fg", "bad")
                self._after_send_failed()
                return

            message = self.cfg.get("message") or "continue"
            auto.SendKeys(self._escape_sendkeys(message), interval=0.03, waitTime=0.2)
            time.sleep(0.3)
            auto.SendKeys("{Enter}", waitTime=0.2)
            self.log("log_sent", "good", message=message)
            self.emit("beep", None)
        except Exception as e:
            self.log("log_send_fail", "bad", err=repr(e))
            self._after_send_failed()
            return

        if manual and self.state not in (self.ARMED, self.VERIFY):
            return
        self.state = self.VERIFY
        self.verify_at = dt.datetime.now() + dt.timedelta(seconds=120)
        self.emit("state", self._state_info())

    def _after_send_failed(self):
        if self.state in (self.ARMED, self.VERIFY):
            self.retries += 1
            if self.retries > self.cfg["max_retries"]:
                self.log("log_retries_done", "warn")
                self.state = self.MONITORING
            else:
                self.send_at = dt.datetime.now() + dt.timedelta(seconds=300)
                self.state = self.ARMED
                self.log("log_retry_at", send=f"{self.send_at:%H:%M:%S}",
                         n=self.retries, max=self.cfg["max_retries"])
            self.emit("state", self._state_info())

    def _verify_after_send(self):
        win = self._get_window()
        if not win:
            self.state = self.MONITORING
            self.emit("state", self._state_info())
            return
        threshold = self.cfg.get("limit_threshold_pct", 100)
        rows, ok = self._read_usage_panel(win)
        if ok:
            self.last_rows = rows
            self.emit("usage", {"rows": rows})
        h5 = rows.get("5h", {})
        pct = h5.get("pct")
        still_hit = ok and pct is not None and pct >= threshold

        if still_hit:
            self.retries += 1
            if self.retries > self.cfg["max_retries"]:
                self.log("log_still_done", "warn")
                self.state = self.MONITORING
            else:
                new_reset = h5.get("reset")
                if new_reset and new_reset > dt.datetime.now() + dt.timedelta(minutes=2):
                    self.reset_at = new_reset
                    self.send_at = new_reset + dt.timedelta(
                        seconds=self.cfg["send_delay_after_reset_s"])
                    self.log("log_still_new", reset=f"{new_reset:%a %H:%M}",
                             send=f"{self.send_at:%H:%M:%S}")
                else:
                    self.send_at = dt.datetime.now() + dt.timedelta(
                        seconds=self.cfg["retry_wait_s"])
                    self.log("log_still_retry", send=f"{self.send_at:%H:%M:%S}",
                             n=self.retries, max=self.cfg["max_retries"])
                self.state = self.ARMED
        else:
            # panel says < threshold (or unreadable) -> treat as resumed
            self.log("log_success", "good")
            self.retries = 0
            self.reset_at = self.send_at = None
            self.state = self.MONITORING
            if self.cfg["keep_awake"]:
                keep_awake(False)
        self.emit("state", self._state_info())

    def _state_info(self):
        return {
            "state": self.state,
            "reset_at": self.reset_at,
            "send_at": self.send_at,
        }

# ------------------------------------------------------------------ UI theme

THEME = {
    "bg":        "#14161B",   # night — warm graphite
    "panel":     "#1C1F26",
    "panel_hi":  "#242935",
    "border":    "#2A2E38",
    "text":      "#E9E4D6",   # parchment under a dimmed lamp
    "muted":     "#8B92A0",
    "log_bg":    "#101216",
    "log_fg":    "#A8B0BE",
    "green":     "#7BAE7F",   # watching
    "amber":     "#E0A458",   # limit / armed
    "amber_dim": "#8A6A3F",
    "blue":      "#7FA8D9",   # verifying
    "red":       "#D98080",
    "track":     "#2A2E38",
}

# state -> lamp color key + label string key
STATE_COLOR = {"IDLE": "muted", "MONITORING": "green",
               "ARMED": "amber", "VERIFY": "blue"}
STATE_LABEL = {"IDLE": "state_idle", "MONITORING": "state_monitoring",
               "ARMED": "state_armed", "VERIFY": "state_verify"}


def pick_fonts(root):
    import tkinter.font as tkfont
    fams = set(tkfont.families(root))
    mono = "Cascadia Mono" if "Cascadia Mono" in fams else "Consolas"
    ui = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI"
    return ui, mono


def enable_dark_titlebar(root):
    try:
        root.update_idletasks()
        hwnd = user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (and older variant)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Auto-Resume")
        self.geometry("680x660")
        self.minsize(620, 560)
        self.configure(bg=THEME["bg"])

        self.cfg = load_config()
        self.lang = self.cfg.get("language", "en")
        self.out_queue = queue.Queue()
        self.worker = MonitorWorker(self.out_queue, self.cfg)
        self.windows = []            # [(hwnd, title)]
        self.state_info = {"state": "IDLE", "reset_at": None, "send_at": None}
        self.usage = {}              # used / plan / reset / session
        self.armed_since = None
        self.last_status = ""        # "" | "no_window" | "ok"

        self.font_ui, self.font_mono = pick_fonts(self)
        self._build_styles()
        self._build_ui()
        self._retext()
        self._update_lang_buttons()
        enable_dark_titlebar(self)

        self.worker.start()
        self.worker.command("refresh_windows")
        self.after(200, self._poll_queue)
        self.after(250, self._tick_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------- style
    def _build_styles(self):
        t = THEME
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=t["bg"], foreground=t["text"],
                        bordercolor=t["border"], focuscolor=t["amber"],
                        lightcolor=t["border"], darkcolor=t["border"],
                        troughcolor=t["bg"], font=(self.font_ui, 10))
        style.configure("TFrame", background=t["bg"])
        style.configure("Panel.TFrame", background=t["panel"])
        style.configure("TLabel", background=t["bg"], foreground=t["text"])
        style.configure("Panel.TLabel", background=t["panel"])
        style.configure("Section.TLabel", background=t["bg"], foreground=t["muted"],
                        font=(self.font_ui, 9, "bold"))
        style.configure("TButton", background=t["panel_hi"], foreground=t["text"],
                        borderwidth=1, padding=(14, 7))
        style.map("TButton",
                  background=[("disabled", t["panel"]), ("active", "#2E3543")],
                  foreground=[("disabled", t["muted"])])
        style.configure("Primary.TButton", background="#2E4433",
                        foreground="#D6EBD8")
        style.map("Primary.TButton",
                  background=[("disabled", t["panel"]), ("active", "#38523E")],
                  foreground=[("disabled", t["muted"])])
        style.configure("Panel.TCheckbutton", background=t["panel"],
                        foreground=t["text"], indicatorbackground=t["panel_hi"],
                        indicatorforeground=t["amber"])
        style.map("Panel.TCheckbutton",
                  background=[("active", t["panel"])],
                  indicatorbackground=[("selected", t["panel_hi"])],
                  indicatorforeground=[("selected", t["amber"])])
        style.configure("TCombobox", fieldbackground=t["panel_hi"],
                        background=t["panel_hi"], foreground=t["text"],
                        arrowcolor=t["text"], selectbackground=t["panel_hi"],
                        selectforeground=t["text"])
        style.map("TCombobox", fieldbackground=[("readonly", t["panel_hi"])])
        style.configure("TEntry", fieldbackground=t["panel_hi"],
                        foreground=t["text"], insertcolor=t["text"])
        style.configure("TSpinbox", fieldbackground=t["panel_hi"],
                        background=t["panel_hi"], foreground=t["text"],
                        arrowcolor=t["text"], insertcolor=t["text"])
        style.configure("Vertical.TScrollbar", background=t["panel_hi"],
                        troughcolor=t["log_bg"], arrowcolor=t["muted"],
                        bordercolor=t["border"], lightcolor=t["panel_hi"],
                        darkcolor=t["panel_hi"], gripcount=0)
        style.map("Vertical.TScrollbar",
                  background=[("active", "#2E3543"), ("pressed", "#38404F")],
                  arrowcolor=[("active", t["text"])])
        self.option_add("*TCombobox*Listbox.background", t["panel_hi"])
        self.option_add("*TCombobox*Listbox.foreground", t["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["amber_dim"])
        self.option_add("*TCombobox*Listbox.selectForeground", t["text"])

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        t = THEME
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # --- top bar: wordmark + EN/PL toggle ---
        top = tk.Frame(root, bg=t["bg"])
        top.pack(fill="x", pady=(0, 8))
        tk.Label(top, text="CLAUDE AUTO-RESUME", bg=t["bg"], fg=t["muted"],
                 font=(self.font_ui, 9, "bold")).pack(side="left")
        seg = tk.Frame(top, bg=t["border"])
        seg.pack(side="right")
        self.lang_btns = {}
        for code in ("en", "pl"):
            b = tk.Label(seg, text=code.upper(), bg=t["panel_hi"], fg=t["muted"],
                         font=(self.font_ui, 8, "bold"), width=3, pady=2,
                         cursor="hand2")
            b.pack(side="left", padx=1, pady=1)
            b.bind("<Button-1>", lambda _e, c=code: self._set_lang(c))
            self.lang_btns[code] = b

        # --- instrument panel: lamp, state, big clock, progress, usage ---
        head = tk.Frame(root, bg=t["panel"], highlightbackground=t["border"],
                        highlightthickness=1)
        head.pack(fill="x")
        head_in = tk.Frame(head, bg=t["panel"])
        head_in.pack(fill="x", padx=14, pady=(12, 10))

        row_state = tk.Frame(head_in, bg=t["panel"])
        row_state.pack(fill="x")
        self.lamp = tk.Canvas(row_state, width=14, height=14, bg=t["panel"],
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=4)
        self.lamp_id = self.lamp.create_oval(2, 2, 12, 12,
                                             fill=t["muted"], outline="")
        self.lbl_state = tk.Label(row_state, text="", bg=t["panel"], fg=t["text"],
                                  font=(self.font_ui, 13, "bold"))
        self.lbl_state.pack(side="left", padx=(8, 0))
        self.lbl_clock = tk.Label(row_state, text="--:--:--", bg=t["panel"],
                                  fg=t["muted"], font=(self.font_mono, 26, "bold"))
        self.lbl_clock.pack(side="right")

        self.lbl_caption = tk.Label(head_in, text="", bg=t["panel"], fg=t["muted"],
                                    font=(self.font_ui, 9), anchor="w")
        self.lbl_caption.pack(fill="x", pady=(2, 8))

        self.progress = tk.Canvas(head_in, height=4, bg=t["track"],
                                  highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress_fill = self.progress.create_rectangle(
            0, 0, 0, 4, fill=t["amber"], outline="")

        row_usage = tk.Frame(head_in, bg=t["panel"])
        row_usage.pack(fill="x", pady=(10, 0))
        self.lbl_used = tk.Label(row_usage, text="", bg=t["panel"],
                                 fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_used.pack(side="left")
        self.bar_used = tk.Canvas(row_usage, width=90, height=5, bg=t["track"],
                                  highlightthickness=0)
        self.bar_used.pack(side="left", padx=(6, 18), pady=1)
        self.bar_used_fill = self.bar_used.create_rectangle(
            0, 0, 0, 5, fill=t["green"], outline="")
        self.lbl_plan = tk.Label(row_usage, text="", bg=t["panel"],
                                 fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_plan.pack(side="left")
        self.bar_plan = tk.Canvas(row_usage, width=90, height=5, bg=t["track"],
                                  highlightthickness=0)
        self.bar_plan.pack(side="left", padx=(6, 18), pady=1)
        self.bar_plan_fill = self.bar_plan.create_rectangle(
            0, 0, 0, 5, fill=t["green"], outline="")
        self.lbl_reset_seen = tk.Label(row_usage, text="", bg=t["panel"],
                                       fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_reset_seen.pack(side="right")

        # ----------------------------------------------------------- control
        self.sec_control = ttk.Label(root, text="", style="Section.TLabel")
        self.sec_control.pack(anchor="w", pady=(14, 4))
        panel = tk.Frame(root, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1)
        panel.pack(fill="x")
        panel_in = tk.Frame(panel, bg=t["panel"])
        panel_in.pack(fill="x", padx=14, pady=10)

        row_win = tk.Frame(panel_in, bg=t["panel"])
        row_win.pack(fill="x", pady=(0, 8))
        self.lbl_window = tk.Label(row_win, text="", bg=t["panel"], fg=t["text"],
                                   font=(self.font_ui, 10))
        self.lbl_window.pack(side="left")
        self.cmb_windows = ttk.Combobox(row_win, state="readonly", width=40)
        self.cmb_windows.pack(side="left", padx=8)
        self.cmb_windows.bind("<<ComboboxSelected>>", self._on_window_selected)
        self.btn_refresh = ttk.Button(
            row_win, text="", command=lambda: self.worker.command("refresh_windows"))
        self.btn_refresh.pack(side="left")

        row_btn = tk.Frame(panel_in, bg=t["panel"])
        row_btn.pack(fill="x", pady=(0, 8))
        self.btn_start = ttk.Button(row_btn, text="", style="Primary.TButton",
                                    command=self._on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row_btn, text="", command=self._on_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        self.btn_send_now = ttk.Button(row_btn, text="", command=self._on_send_now)
        self.btn_send_now.pack(side="right")

        row_opt = tk.Frame(panel_in, bg=t["panel"])
        row_opt.pack(fill="x", pady=(0, 8))
        self.lbl_scan_every = tk.Label(row_opt, text="", bg=t["panel"],
                                       fg=t["text"], font=(self.font_ui, 10))
        self.lbl_scan_every.pack(side="left")
        self.var_interval = tk.IntVar(value=self.cfg["scan_interval_s"])
        ttk.Spinbox(row_opt, from_=5, to=300, width=4,
                    textvariable=self.var_interval,
                    command=self._push_config).pack(side="left", padx=4)
        self.lbl_seconds = tk.Label(row_opt, text="", bg=t["panel"], fg=t["text"],
                                    font=(self.font_ui, 10))
        self.lbl_seconds.pack(side="left", padx=(0, 16))
        self.var_autosend = tk.BooleanVar(value=self.cfg["auto_send"])
        self.chk_autosend = ttk.Checkbutton(
            row_opt, text="", style="Panel.TCheckbutton",
            variable=self.var_autosend, command=self._push_config)
        self.chk_autosend.pack(side="left", padx=(0, 16))
        self.var_awake = tk.BooleanVar(value=self.cfg["keep_awake"])
        self.chk_awake = ttk.Checkbutton(
            row_opt, text="", style="Panel.TCheckbutton",
            variable=self.var_awake, command=self._push_config)
        self.chk_awake.pack(side="left")

        row_msg = tk.Frame(panel_in, bg=t["panel"])
        row_msg.pack(fill="x", pady=(0, 8))
        self.lbl_message = tk.Label(row_msg, text="", bg=t["panel"],
                                    fg=t["text"], font=(self.font_ui, 10))
        self.lbl_message.pack(side="left")
        self.var_message = tk.StringVar(value=self.cfg.get("message", "continue"))
        self.ent_message = ttk.Entry(row_msg, width=32, textvariable=self.var_message)
        self.ent_message.pack(side="left", padx=8)
        self.ent_message.bind("<FocusOut>", lambda _e: self._commit_message())
        self.ent_message.bind("<Return>", lambda _e: self._commit_message())

        row_manual = tk.Frame(panel_in, bg=t["panel"])
        row_manual.pack(fill="x")
        self.lbl_know_reset = tk.Label(row_manual, text="", bg=t["panel"],
                                       fg=t["text"], font=(self.font_ui, 10))
        self.lbl_know_reset.pack(side="left")
        self.ent_manual = ttk.Entry(row_manual, width=7)
        self.ent_manual.pack(side="left", padx=8)
        self.ent_manual.bind("<Return>", lambda _e: self._on_arm_manual())
        self.btn_arm = ttk.Button(row_manual, text="", command=self._on_arm_manual)
        self.btn_arm.pack(side="left")
        self.lbl_arm_hint = tk.Label(row_manual, text="", bg=t["panel"],
                                     fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_arm_hint.pack(side="left", padx=10)

        # -------------------------------------------------------------- log
        self.sec_log = ttk.Label(root, text="", style="Section.TLabel")
        self.sec_log.pack(anchor="w", pady=(14, 4))
        log_wrap = tk.Frame(root, bg=t["border"])
        log_wrap.pack(fill="both", expand=True)
        self.txt_log = tk.Text(
            log_wrap, height=10, state="disabled", font=(self.font_mono, 9),
            bg=t["log_bg"], fg=t["log_fg"], insertbackground=t["text"],
            relief="flat", highlightthickness=0, wrap="word",
            selectbackground=t["amber_dim"], padx=10, pady=8)
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical",
                                   command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=log_scroll.set)
        self.txt_log.pack(side="left", fill="both", expand=True,
                          padx=(1, 0), pady=1)
        log_scroll.pack(side="right", fill="y", pady=1, padx=(0, 1))
        self.txt_log.tag_configure("warn", foreground=t["amber"])
        self.txt_log.tag_configure("good", foreground=t["green"])
        self.txt_log.tag_configure("bad", foreground=t["red"])

        self.progress.bind("<Configure>", lambda _e: self._draw_progress())

    # ------------------------------------------------------------------ i18n
    def _T(self, key, **kw):
        return tr(self.lang, key, **kw)

    def _set_lang(self, code):
        if code == self.lang or code not in ("en", "pl"):
            return
        self.lang = code
        self.cfg["language"] = code
        save_config(self.cfg)
        self.worker.command("config", {"language": code})
        self._update_lang_buttons()
        self._retext()

    def _update_lang_buttons(self):
        for code, btn in self.lang_btns.items():
            active = code == self.lang
            btn.config(fg=THEME["amber"] if active else THEME["muted"],
                       bg=THEME["panel_hi"] if active else THEME["panel"])

    def _retext(self):
        """Re-apply all static strings in the current language."""
        self.sec_control.config(text=self._T("sec_control"))
        self.sec_log.config(text=self._T("sec_log"))
        self.lbl_window.config(text=self._T("lbl_window"))
        self.btn_refresh.config(text=self._T("btn_refresh"))
        self.btn_start.config(text=self._T("btn_start"))
        self.btn_stop.config(text=self._T("btn_stop"))
        self.btn_send_now.config(text=self._T("btn_send_now"))
        self.lbl_scan_every.config(text=self._T("lbl_scan_every"))
        self.lbl_seconds.config(text=self._T("lbl_seconds"))
        self.chk_autosend.config(text=self._T("chk_autosend"))
        self.chk_awake.config(text=self._T("chk_awake"))
        self.lbl_message.config(text=self._T("lbl_message"))
        self.lbl_know_reset.config(text=self._T("lbl_know_reset"))
        self.btn_arm.config(text=self._T("btn_arm"))
        self.lbl_arm_hint.config(text=self._T("lbl_arm_hint"))
        self._rebuild_window_combo()
        self._render_state()
        self._render_usage()
        self._update_caption()

    # ----------------------------------------------------------------- events
    def _on_window_selected(self, _event):
        idx = self.cmb_windows.current()
        if 0 <= idx < len(self.windows):
            self.worker.command("select_window", self.windows[idx][0])

    def _on_start(self):
        self._push_config()
        self.worker.command("start")

    def _on_stop(self):
        self.worker.command("stop")

    def _on_send_now(self):
        if messagebox.askyesno(self._T("dlg_send_title"), self._T("dlg_send_body")):
            self.worker.command("send_now")

    def _on_arm_manual(self):
        raw = self.ent_manual.get().strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m:
            messagebox.showerror(self._T("dlg_time_title"),
                                 self._T("dlg_time_format"))
            return
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            messagebox.showerror(self._T("dlg_time_title"),
                                 self._T("dlg_time_range"))
            return
        t = dt.datetime.now().replace(hour=h, minute=mi, second=0, microsecond=0)
        if t <= dt.datetime.now():
            t += dt.timedelta(days=1)
        self._push_config()
        self.worker.command("arm_manual", t)

    def _commit_message(self):
        """Save the custom message; fall back to 'continue' when left blank."""
        msg = self.var_message.get().strip()
        if not msg:
            msg = "continue"
            self.var_message.set(msg)
        self._push_config()

    def _push_config(self):
        try:
            interval = max(5, int(self.var_interval.get()))
        except (tk.TclError, ValueError):
            interval = DEFAULT_CONFIG["scan_interval_s"]
        message = self.var_message.get().strip() or "continue"
        payload = {
            "scan_interval_s": interval,
            "auto_send": bool(self.var_autosend.get()),
            "keep_awake": bool(self.var_awake.get()),
            "message": message,
        }
        self.cfg.update(payload)
        save_config(self.cfg)
        self.worker.command("config", payload)

    # ------------------------------------------------------------- worker queue
    def _poll_queue(self):
        try:
            while True:
                kind, data = self.out_queue.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        self.after(200, self._poll_queue)

    def _handle_event(self, kind, data):
        if kind == "log":
            line, level = data
            tag = (level,) if level in ("warn", "good", "bad") else ()
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", line + "\n", tag)
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        elif kind == "windows":
            self.windows = data
            self._rebuild_window_combo()
            if data and self.cmb_windows.current() < 0:
                self.cmb_windows.current(0)
                self.worker.command("select_window", data[0][0])
            if not data:
                self.last_status = "no_window"
                self._update_caption()
        elif kind == "usage":
            self.usage.update(data)
            self._render_usage()
        elif kind == "status":
            self.last_status = data
        elif kind in ("state", "countdown"):
            prev = self.state_info.get("state")
            self.state_info = data
            if data["state"] == "ARMED" and prev != "ARMED":
                self.armed_since = dt.datetime.now()
            elif data["state"] != "ARMED":
                self.armed_since = None
            self._render_state()
        elif kind == "beep":
            try:
                import winsound
                winsound.MessageBeep()
            except Exception:
                pass

    # ------------------------------------------------------------------ drawing
    def _rebuild_window_combo(self):
        vals = [self._T("combo_handle", title=title, hwnd=hwnd)
                for hwnd, title in self.windows]
        keep = self.cmb_windows.current()
        self.cmb_windows["values"] = vals
        if 0 <= keep < len(vals):
            self.cmb_windows.current(keep)

    def _render_usage(self):
        rows = self.usage.get("rows", {})

        def bar(canvas, fill_id, pct):
            width = canvas.winfo_width() or 90
            frac = max(0, min(100, pct)) / 100
            color = (THEME["red"] if pct >= 100
                     else THEME["amber"] if pct >= 80 else THEME["green"])
            canvas.coords(fill_id, 0, 0, int(width * frac), 5)
            canvas.itemconfigure(fill_id, fill=color)

        h5 = rows.get("5h", {})
        wk = rows.get("weekly", {})
        fb = rows.get("weekly_fable", {})

        if "pct" in h5:
            self.lbl_used.config(text=self._T("usage_5h", pct=h5["pct"]))
            bar(self.bar_used, self.bar_used_fill, h5["pct"])
        else:
            self.lbl_used.config(text=self._T("usage_5h_none"))
            self.bar_used.coords(self.bar_used_fill, 0, 0, 0, 5)

        if "pct" in wk:
            self.lbl_plan.config(text=self._T("usage_weekly", pct=wk["pct"]))
            bar(self.bar_plan, self.bar_plan_fill, wk["pct"])
        else:
            self.lbl_plan.config(text="")
            self.bar_plan.coords(self.bar_plan_fill, 0, 0, 0, 5)

        parts = []
        if "pct" in fb:
            parts.append(self._T("usage_fable", pct=fb["pct"]))
        if "reset" in h5:
            parts.append(self._T("usage_5h_reset", reset=f"{h5['reset']:%a %H:%M}"))
        self.lbl_reset_seen.config(text="  ·  ".join(parts))

    def _render_state(self):
        st = self.state_info["state"]
        running = st != "IDLE"
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")
        self.lbl_state.config(text=self._T(STATE_LABEL[st]))
        self.lamp.itemconfigure(self.lamp_id, fill=THEME[STATE_COLOR[st]])

    def _update_caption(self):
        """Set the header caption from current state (language-aware)."""
        st = self.state_info["state"]
        now = dt.datetime.now()
        if st == "IDLE":
            self.lbl_caption.config(text=self._T("cap_click_start"))
        elif st == "MONITORING":
            if self.last_status == "no_window":
                self.lbl_caption.config(text=self._T("cap_no_window"))
            elif self.usage.get("session"):
                self.lbl_caption.config(
                    text=self._T("cap_guarding",
                                 session=self.usage["session"][:60]))
            else:
                self.lbl_caption.config(
                    text=self._T("cap_scanning", sec=self.cfg["scan_interval_s"]))
        elif st == "ARMED":
            send_at = self.state_info.get("send_at")
            if send_at:
                reset_at = self.state_info.get("reset_at")
                if reset_at:
                    self.lbl_caption.config(
                        text=self._T("cap_armed_reset",
                                     reset=f"{reset_at:%a %H:%M}",
                                     send=f"{send_at:%H:%M:%S}"))
                else:
                    self.lbl_caption.config(
                        text=self._T("cap_armed_noreset",
                                     send=f"{send_at:%H:%M:%S}"))
        elif st == "VERIFY":
            self.lbl_caption.config(text=self._T("cap_verify"))

    def _tick_ui(self):
        st = self.state_info["state"]
        now = dt.datetime.now()
        t = THEME

        if st == "IDLE":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["muted"])
        elif st == "MONITORING":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["text"])
        elif st == "ARMED":
            send_at = self.state_info.get("send_at")
            if send_at:
                secs = max(0, int((send_at - now).total_seconds()))
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                self.lbl_clock.config(text=f"{h:02d}:{m:02d}:{s:02d}", fg=t["amber"])
            blink_on = int(time.time()) % 2 == 0
            self.lamp.itemconfigure(
                self.lamp_id, fill=t["amber"] if blink_on else t["amber_dim"])
        elif st == "VERIFY":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["blue"])

        self._update_caption()
        self._draw_progress()
        self.after(250, self._tick_ui)

    def _draw_progress(self):
        width = self.progress.winfo_width() or 1
        frac = 0.0
        send_at = self.state_info.get("send_at")
        if (self.state_info["state"] == "ARMED" and send_at and self.armed_since
                and send_at > self.armed_since):
            total = (send_at - self.armed_since).total_seconds()
            done = (dt.datetime.now() - self.armed_since).total_seconds()
            frac = max(0.0, min(1.0, done / total))
        self.progress.coords(self.progress_fill, 0, 0, int(width * frac), 4)

    def _on_close(self):
        self.worker.shutdown()
        allow_sleep()
        self.destroy()


def main():
    # crisp DPI on Windows 11
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
