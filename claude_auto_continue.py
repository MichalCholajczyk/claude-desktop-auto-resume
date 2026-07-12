# -*- coding: utf-8 -*-
"""
Claude Auto-Continue — auto-wznawianie sesji Claude Desktop (Windows 11).

Monitoruje okno aplikacji Claude przez UI Automation, wykrywa komunikat
o wyczerpaniu limitu (5h / tygodniowego), parsuje godzinę resetu i minutę
po resecie wpisuje "continue" w pole czatu i wciska Enter.

Wymaga: Python 3.10+, pakiet `uiautomation` (pip install uiautomation).
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
        "Brak pakietu 'uiautomation'.\n\nZainstaluj:  py -m pip install uiautomation",
        "Claude Auto-Continue", 0x10)
    sys.exit(1)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "auto_continue_config.json")
LOG_PATH = os.path.join(APP_DIR, "auto_continue.log")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ---------------------------------------------------------------- konfiguracja

DEFAULT_CONFIG = {
    "scan_interval_s": 20,          # co ile sekund skanować okno
    "send_delay_after_reset_s": 60, # ile sekund po resecie wysłać "continue"
    "message": "continue",          # co wpisać w czat
    "auto_send": True,              # False = tylko alarmuj, nie wysyłaj
    "keep_awake": True,             # nie pozwól Windowsowi zasnąć
    "max_retries": 6,               # ile razy ponawiać, jeśli limit dalej aktywny
    "retry_wait_s": 600,            # odstęp między ponowieniami bez znanej godziny resetu
    # dodatkowe wzorce (regex, case-insensitive) traktowane jako "limit strzelony"
    "extra_hard_patterns": [],
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

# ------------------------------------------------------------------- detekcja

# Frazy oznaczające TWARDY limit (sesja zablokowana) — nie zwykłe ostrzeżenie.
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

# "Resets Mon, Jul 13, 6:00 PM" / "resets 3pm" / "resets at 6:30 PM" / "resetuje się o 15:00"
RE_RESET_ABS = re.compile(
    r"reset(?:s|uje(?:\s*si[eę])?)?\s*(?:at\s+|o\s+|:\s*)?"
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun|pon|wt|śr|czw|pt|sob|ndz?|nie)[a-ząćęłńóśźż]*\.?,?\s+)?"
    r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|sty|lut|kwi|maj|cze|lip|sie|wrz|paź|lis|gru)"
    r"[a-ząćęłńóśźż]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
    re.IGNORECASE)

# "Resets in 2 hr 15 min" / "reset za 2 godz. 15 min"
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

# Grupy UI, których treść ignorujemy (żeby rozmowa o "limitach" nie robiła fałszywych alarmów)
EXCLUDED_GROUPS = {"chat messages", "sidebar", "recents"}


def parse_reset_time(text, now=None):
    """Zwraca datetime resetu wyciągnięty z tekstu albo None."""
    now = now or dt.datetime.now()

    m = RE_RESET_ABS.search(text)
    if m:
        mon, day, hh, mm, ampm = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        # odrzuć dopasowania bez minut/AM-PM/daty (np. "resets 5" z przypadkowego tekstu)
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
    """Zwraca dopasowaną frazę twardego limitu albo None."""
    for pat in list(HARD_LIMIT_PATTERNS) + list(extra_patterns):
        try:
            m = re.search(pat, text, re.IGNORECASE)
        except re.error:
            continue
        if m:
            return m.group(0)
    return None

# ------------------------------------------------------------ warstwa Windows

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_awake(armed):
    """Nie pozwól systemowi zasnąć; gdy uzbrojony — trzymaj też ekran."""
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

# --------------------------------------------------------------- wątek monitora


class MonitorWorker(threading.Thread):
    """Cała komunikacja z UI Automation odbywa się w tym wątku (COM apartment)."""

    IDLE, MONITORING, ARMED, VERIFY = "IDLE", "MONITORING", "ARMED", "VERIFY"

    def __init__(self, out_queue, cfg):
        super().__init__(daemon=True)
        self.out = out_queue          # (typ, dane) -> UI
        self.cmds = queue.Queue()     # komendy z UI
        self.cfg = cfg
        self.state = self.IDLE
        self.hwnd = None
        self.reset_at = None
        self.send_at = None
        self.verify_at = None
        self.retries = 0
        self.next_scan = 0.0
        self.miss_count = 0
        self._stop = threading.Event()

    # --- API dla wątku UI (thread-safe) ---
    def command(self, name, payload=None):
        self.cmds.put((name, payload))

    def shutdown(self):
        self._stop.set()

    # --- komunikaty do UI ---
    def emit(self, kind, data=None):
        self.out.put((kind, data))

    def log(self, msg):
        line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        self.emit("log", line)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------- pętla główna
    def run(self):
        with auto.UIAutomationInitializerInThread():
            auto.SetGlobalSearchTimeout(3)
            while not self._stop.is_set():
                try:
                    self._process_commands()
                    self._tick()
                except Exception as e:  # monitor nie może umrzeć po cichu
                    self.log(f"BŁĄD monitora: {e!r}")
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
                    self.log("Nie znaleziono okna Claude — uruchom aplikację Claude i odśwież.")
                    continue
                self.state = self.MONITORING
                self.retries = 0
                self.next_scan = 0.0
                if self.cfg["keep_awake"]:
                    keep_awake(False)
                self.log("Monitoring uruchomiony.")
                self.emit("state", self._state_info())
            elif name == "stop":
                self.state = self.IDLE
                self.reset_at = self.send_at = None
                allow_sleep()
                self.log("Monitoring zatrzymany.")
                self.emit("state", self._state_info())
            elif name == "send_now":
                self.log("Ręczna wysyłka 'continue'...")
                self._do_send(manual=True)
            elif name == "arm_manual":
                self.reset_at = payload
                self.send_at = payload + dt.timedelta(seconds=self.cfg["send_delay_after_reset_s"])
                self.state = self.ARMED
                self.retries = 0
                if self.cfg["keep_awake"]:
                    keep_awake(True)
                self.log(f"Uzbrojono ręcznie: reset {payload:%Y-%m-%d %H:%M}, "
                         f"wysyłka {self.send_at:%H:%M:%S}.")
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
            if dt.datetime.now() >= self.send_at:
                if self.cfg["auto_send"]:
                    self._do_send()
                else:
                    self.log("Czas wysyłki minął, ale auto-wysyłka jest WYŁĄCZONA — tylko alarm.")
                    self.emit("beep", None)
                    self.state = self.MONITORING
                    self.emit("state", self._state_info())
            elif now >= self.next_scan:
                # odświeżaj godzinę resetu (baner może się zaktualizować)
                self.next_scan = now + max(60, self.cfg["scan_interval_s"])
                self._scan_and_decide(refresh_only=True)
            self.emit("countdown", self._state_info())
        elif self.state == self.VERIFY:
            if dt.datetime.now() >= self.verify_at:
                self._verify_after_send()

    # ---------------------------------------------------------------- skanowanie
    def _get_window(self):
        if not self.hwnd or not user32.IsWindow(self.hwnd):
            wins = self._enum_windows()
            if wins:
                self.hwnd = wins[0][0]
                self.emit("windows", wins)
                self.log(f"Okno Claude odnalezione ponownie (uchwyt {self.hwnd}).")
            else:
                return None
        try:
            return auto.ControlFromHandle(self.hwnd)
        except Exception:
            return None

    def _enum_windows(self):
        """Lista (hwnd, tytuł) okien procesu claude.exe."""
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
        """Chromium buduje drzewo dostępności dopiero, gdy klient o nie pyta."""
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
        """Zwraca (teksty_poza_czatem, kontrolka_prompt, tytuł_sesji)."""
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
                    continue        # wnętrze wykluczonej grupy (czat/sidebar)
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

            # najlepszy strzał w tytuł aktywnej sesji: tekst w górnym pasku
            if session_title is None and win_rect and ct in ("ButtonControl", "TextControl"):
                try:
                    r = ctrl.BoundingRectangle
                    if 30 < r.top < 90 and (r.right - r.left) > 120 and name.strip() and \
                            name.strip().lower() not in ("search", "menu", "back", "forward"):
                        session_title = name.strip()
                except Exception:
                    pass

        return texts, prompt, session_title

    def _scan_and_decide(self, refresh_only=False):
        win = self._get_window()
        if not win:
            self.emit("status", "Nie widzę okna Claude — czekam...")
            return
        texts, _prompt, session = self._collect(win)
        joined = "  ".join(texts)

        info = {}
        m = RE_USED_PCT.search(joined)
        if m:
            info["used"] = int(m.group(1))
        m = RE_PLAN_PCT.search(joined)
        if m:
            info["plan"] = int(m.group(1))
        reset_seen = parse_reset_time(joined)
        if reset_seen:
            info["reset"] = reset_seen
        if session:
            info["session"] = session
        self.emit("usage", info)

        hard = find_hard_limit(joined, self.cfg.get("extra_hard_patterns", ()))
        if not hard:
            if self.state == self.ARMED:
                # komunikat zniknął na długo przed resetem -> pewnie wznowiono ręcznie
                if self.reset_at and dt.datetime.now() < self.reset_at - dt.timedelta(minutes=3):
                    self.miss_count += 1
                    if self.miss_count >= 3:
                        self.log("Komunikat o limicie zniknął przed resetem — "
                                 "rozbrajam i wracam do monitoringu.")
                        self.state = self.MONITORING
                        self.reset_at = self.send_at = None
                        self.miss_count = 0
                        if self.cfg["keep_awake"]:
                            keep_awake(False)
                        self.emit("state", self._state_info())
            elif self.state == self.MONITORING:
                self.emit("status", "OK — limit nie jest strzelony.")
            return

        # twardy limit wykryty
        self.miss_count = 0
        if reset_seen:
            new_send = reset_seen + dt.timedelta(seconds=self.cfg["send_delay_after_reset_s"])
            if self.state != self.ARMED or self.send_at is None or \
                    abs((new_send - self.send_at).total_seconds()) > 90:
                self.reset_at = reset_seen
                self.send_at = new_send
                if self.state != self.ARMED:
                    self.log(f"LIMIT WYKRYTY ({hard!r}). Reset: {reset_seen:%a %H:%M}. "
                             f"Wyślę 'continue' o {new_send:%H:%M:%S}.")
                    self.emit("beep", None)
                else:
                    self.log(f"Zaktualizowano czas resetu: {reset_seen:%a %H:%M}.")
                self.state = self.ARMED
                if self.cfg["keep_awake"]:
                    keep_awake(True)
                self.emit("state", self._state_info())
        else:
            if self.state != self.ARMED:
                fallback = dt.datetime.now() + dt.timedelta(seconds=self.cfg["retry_wait_s"])
                self.reset_at = None
                self.send_at = fallback
                self.state = self.ARMED
                self.log(f"LIMIT WYKRYTY ({hard!r}), ale nie umiem odczytać godziny resetu. "
                         f"Spróbuję wysłać o {fallback:%H:%M:%S} i będę ponawiać.")
                self.emit("beep", None)
                if self.cfg["keep_awake"]:
                    keep_awake(True)
                self.emit("state", self._state_info())

    # ------------------------------------------------------------------ wysyłka
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
            self.log("WYSYŁKA NIEUDANA: brak okna Claude.")
            self._after_send_failed()
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
                # awaryjnie: klik nad dolnym paskiem przycisków, na środku okna
                r = win.BoundingRectangle
                auto.Click(int((r.left + r.right) / 2), int(r.bottom - 80))
            time.sleep(0.6)

            if user32.GetForegroundWindow() != hwnd:
                self._focus_window(hwnd)
                time.sleep(0.4)
            if user32.GetForegroundWindow() != hwnd:
                self.log("WYSYŁKA PRZERWANA: okno Claude nie jest na wierzchu "
                         "(nie będę pisać do innej aplikacji).")
                self._after_send_failed()
                return

            auto.SendKeys(self.cfg["message"], interval=0.03, waitTime=0.2)
            time.sleep(0.3)
            auto.SendKeys("{Enter}", waitTime=0.2)
            self.log(f"Wysłano '{self.cfg['message']}' + Enter.")
            self.emit("beep", None)
        except Exception as e:
            self.log(f"WYSYŁKA NIEUDANA: {e!r}")
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
                self.log("Wyczerpano próby — wracam do zwykłego monitoringu.")
                self.state = self.MONITORING
            else:
                self.send_at = dt.datetime.now() + dt.timedelta(seconds=300)
                self.state = self.ARMED
                self.log(f"Ponowna próba o {self.send_at:%H:%M:%S} "
                         f"({self.retries}/{self.cfg['max_retries']}).")
            self.emit("state", self._state_info())

    def _verify_after_send(self):
        win = self._get_window()
        if not win:
            self.state = self.MONITORING
            self.emit("state", self._state_info())
            return
        texts, _, _ = self._collect(win)
        joined = "  ".join(texts)
        hard = find_hard_limit(joined, self.cfg.get("extra_hard_patterns", ()))
        if hard:
            self.retries += 1
            if self.retries > self.cfg["max_retries"]:
                self.log("Limit nadal aktywny, wyczerpano próby — monitoruję dalej.")
                self.state = self.MONITORING
            else:
                new_reset = parse_reset_time(joined)
                if new_reset and new_reset > dt.datetime.now() + dt.timedelta(minutes=2):
                    self.reset_at = new_reset
                    self.send_at = new_reset + dt.timedelta(
                        seconds=self.cfg["send_delay_after_reset_s"])
                    self.log(f"Limit nadal aktywny — nowy reset {new_reset:%a %H:%M}, "
                             f"wysyłka o {self.send_at:%H:%M:%S}.")
                else:
                    self.send_at = dt.datetime.now() + dt.timedelta(
                        seconds=self.cfg["retry_wait_s"])
                    self.log(f"Limit nadal aktywny — ponowię o {self.send_at:%H:%M:%S} "
                             f"({self.retries}/{self.cfg['max_retries']}).")
                self.state = self.ARMED
        else:
            self.log("SUKCES — limit zniknął, sesja wznowiona. Monitoruję dalej.")
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

# ------------------------------------------------------------------ motyw UI

THEME = {
    "bg":        "#14161B",   # noc — ciepły grafit
    "panel":     "#1C1F26",
    "panel_hi":  "#242935",
    "border":    "#2A2E38",
    "text":      "#E9E4D6",   # pergamin przy przygaszonej lampie
    "muted":     "#8B92A0",
    "log_bg":    "#101216",
    "log_fg":    "#A8B0BE",
    "green":     "#7BAE7F",   # czuwanie
    "amber":     "#E0A458",   # limit / uzbrojenie
    "amber_dim": "#8A6A3F",
    "blue":      "#7FA8D9",   # weryfikacja
    "red":       "#D98080",
    "track":     "#2A2E38",
}

STATE_VIEW = {
    "IDLE":       ("Czuwanie wyłączone", "muted"),
    "MONITORING": ("Czuwam nad sesją", "green"),
    "ARMED":      ("Limit strzelony — czekam na reset", "amber"),
    "VERIFY":     ("Wysłano — sprawdzam efekt", "blue"),
}


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
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (i starszy wariant)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Claude Auto-Continue")
        self.geometry("680x640")
        self.minsize(620, 540)
        self.configure(bg=THEME["bg"])

        self.cfg = load_config()
        self.out_queue = queue.Queue()
        self.worker = MonitorWorker(self.out_queue, self.cfg)
        self.windows = []            # [(hwnd, tytuł)]
        self.state_info = {"state": "IDLE", "reset_at": None, "send_at": None}
        self.usage = {}              # used / plan / reset / session
        self.armed_since = None
        self._blink = False

        self.font_ui, self.font_mono = pick_fonts(self)
        self._build_styles()
        self._build_ui()
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
        style.configure("Muted.TLabel", background=t["bg"], foreground=t["muted"],
                        font=(self.font_ui, 9))
        style.configure("PanelMuted.TLabel", background=t["panel"],
                        foreground=t["muted"], font=(self.font_ui, 9))
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
        # lista rozwijana comboboksa (zwykły Listbox tk)
        self.option_add("*TCombobox*Listbox.background", t["panel_hi"])
        self.option_add("*TCombobox*Listbox.foreground", t["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["amber_dim"])
        self.option_add("*TCombobox*Listbox.selectForeground", t["text"])

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        t = THEME
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # --- pasek instrumentu: lampka, stan, wielki zegar, postęp, zużycie ---
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
        self.lbl_state = tk.Label(row_state, text="Czuwanie wyłączone",
                                  bg=t["panel"], fg=t["text"],
                                  font=(self.font_ui, 13, "bold"))
        self.lbl_state.pack(side="left", padx=(8, 0))
        self.lbl_clock = tk.Label(row_state, text="--:--:--", bg=t["panel"],
                                  fg=t["muted"], font=(self.font_mono, 26, "bold"))
        self.lbl_clock.pack(side="right")

        self.lbl_caption = tk.Label(head_in, text="Kliknij „Rozpocznij czuwanie”.",
                                    bg=t["panel"], fg=t["muted"],
                                    font=(self.font_ui, 9), anchor="w")
        self.lbl_caption.pack(fill="x", pady=(2, 8))

        self.progress = tk.Canvas(head_in, height=4, bg=t["track"],
                                  highlightthickness=0)
        self.progress.pack(fill="x")
        self.progress_fill = self.progress.create_rectangle(
            0, 0, 0, 4, fill=t["amber"], outline="")

        row_usage = tk.Frame(head_in, bg=t["panel"])
        row_usage.pack(fill="x", pady=(10, 0))
        self.lbl_used = tk.Label(row_usage, text="limit modelu —", bg=t["panel"],
                                 fg=t["muted"], font=(self.font_ui, 9))
        self.lbl_used.pack(side="left")
        self.bar_used = tk.Canvas(row_usage, width=90, height=5, bg=t["track"],
                                  highlightthickness=0)
        self.bar_used.pack(side="left", padx=(6, 18), pady=1)
        self.bar_used_fill = self.bar_used.create_rectangle(
            0, 0, 0, 5, fill=t["green"], outline="")
        self.lbl_plan = tk.Label(row_usage, text="plan —", bg=t["panel"],
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

        # ----------------------------------------------------------- sterowanie
        ttk.Label(root, text="STEROWANIE", style="Section.TLabel").pack(
            anchor="w", pady=(14, 4))
        panel = tk.Frame(root, bg=t["panel"], highlightbackground=t["border"],
                         highlightthickness=1)
        panel.pack(fill="x")
        panel_in = tk.Frame(panel, bg=t["panel"])
        panel_in.pack(fill="x", padx=14, pady=10)

        row_win = tk.Frame(panel_in, bg=t["panel"])
        row_win.pack(fill="x", pady=(0, 8))
        tk.Label(row_win, text="Okno Claude:", bg=t["panel"], fg=t["text"],
                 font=(self.font_ui, 10)).pack(side="left")
        self.cmb_windows = ttk.Combobox(row_win, state="readonly", width=40)
        self.cmb_windows.pack(side="left", padx=8)
        self.cmb_windows.bind("<<ComboboxSelected>>", self._on_window_selected)
        ttk.Button(row_win, text="Odśwież", command=lambda: self.worker.command(
            "refresh_windows")).pack(side="left")

        row_btn = tk.Frame(panel_in, bg=t["panel"])
        row_btn.pack(fill="x", pady=(0, 8))
        self.btn_start = ttk.Button(row_btn, text="Rozpocznij czuwanie",
                                    style="Primary.TButton", command=self._on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row_btn, text="Zatrzymaj",
                                   command=self._on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(row_btn, text="Wyślij „continue” teraz",
                   command=self._on_send_now).pack(side="right")

        row_opt = tk.Frame(panel_in, bg=t["panel"])
        row_opt.pack(fill="x", pady=(0, 8))
        tk.Label(row_opt, text="Skanuj co", bg=t["panel"], fg=t["text"],
                 font=(self.font_ui, 10)).pack(side="left")
        self.var_interval = tk.IntVar(value=self.cfg["scan_interval_s"])
        ttk.Spinbox(row_opt, from_=5, to=300, width=4,
                    textvariable=self.var_interval,
                    command=self._push_config).pack(side="left", padx=4)
        tk.Label(row_opt, text="s", bg=t["panel"], fg=t["text"],
                 font=(self.font_ui, 10)).pack(side="left", padx=(0, 16))
        self.var_autosend = tk.BooleanVar(value=self.cfg["auto_send"])
        ttk.Checkbutton(row_opt, text="Wysyłaj automatycznie",
                        style="Panel.TCheckbutton", variable=self.var_autosend,
                        command=self._push_config).pack(side="left", padx=(0, 16))
        self.var_awake = tk.BooleanVar(value=self.cfg["keep_awake"])
        ttk.Checkbutton(row_opt, text="Nie usypiaj komputera",
                        style="Panel.TCheckbutton", variable=self.var_awake,
                        command=self._push_config).pack(side="left")

        row_manual = tk.Frame(panel_in, bg=t["panel"])
        row_manual.pack(fill="x")
        tk.Label(row_manual, text="Znasz godzinę resetu?", bg=t["panel"],
                 fg=t["text"], font=(self.font_ui, 10)).pack(side="left")
        self.ent_manual = ttk.Entry(row_manual, width=7)
        self.ent_manual.pack(side="left", padx=8)
        self.ent_manual.bind("<Return>", lambda _e: self._on_arm_manual())
        ttk.Button(row_manual, text="Uzbrój",
                   command=self._on_arm_manual).pack(side="left")
        tk.Label(row_manual, text="format HH:MM — wyślę minutę po tej godzinie",
                 bg=t["panel"], fg=t["muted"],
                 font=(self.font_ui, 9)).pack(side="left", padx=10)

        # -------------------------------------------------------------- dziennik
        ttk.Label(root, text="DZIENNIK", style="Section.TLabel").pack(
            anchor="w", pady=(14, 4))
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

    # ----------------------------------------------------------------- zdarzenia
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
        if messagebox.askyesno(
                "Potwierdź wysyłkę",
                "Wpisać „continue” i wcisnąć Enter w oknie Claude teraz?"):
            self.worker.command("send_now")

    def _on_arm_manual(self):
        raw = self.ent_manual.get().strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m:
            messagebox.showerror(
                "Nieprawidłowa godzina",
                "Wpisz godzinę resetu w formacie HH:MM, np. 15:00.")
            return
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            messagebox.showerror(
                "Nieprawidłowa godzina",
                "Godzina musi być z zakresu 00:00–23:59.")
            return
        t = dt.datetime.now().replace(hour=h, minute=mi, second=0, microsecond=0)
        if t <= dt.datetime.now():
            t += dt.timedelta(days=1)
        self._push_config()
        self.worker.command("arm_manual", t)

    def _push_config(self):
        try:
            interval = max(5, int(self.var_interval.get()))
        except (tk.TclError, ValueError):
            interval = DEFAULT_CONFIG["scan_interval_s"]
        payload = {
            "scan_interval_s": interval,
            "auto_send": bool(self.var_autosend.get()),
            "keep_awake": bool(self.var_awake.get()),
        }
        self.cfg.update(payload)
        save_config(self.cfg)
        self.worker.command("config", payload)

    # ------------------------------------------------------------- kolejka z wątku
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
            tag = ()
            if any(k in data for k in ("NIEUDANA", "PRZERWANA", "BŁĄD")):
                tag = ("bad",)
            elif "LIMIT WYKRYTY" in data:
                tag = ("warn",)
            elif "SUKCES" in data or "Wysłano" in data:
                tag = ("good",)
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", data + "\n", tag)
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        elif kind == "windows":
            self.windows = data
            vals = [f"{title}  (uchwyt {hwnd})" for hwnd, title in data]
            self.cmb_windows["values"] = vals
            if data and self.cmb_windows.current() < 0:
                self.cmb_windows.current(0)
                self.worker.command("select_window", data[0][0])
            if not data:
                self.lbl_caption.config(text="Nie widzę okna Claude — uruchom "
                                             "aplikację i kliknij „Odśwież”.")
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

    # ------------------------------------------------------------------ rysowanie
    def _render_usage(self):
        u = self.usage

        def bar(canvas, fill_id, pct):
            width = canvas.winfo_width() or 90
            frac = max(0, min(100, pct)) / 100
            color = (THEME["red"] if pct >= 100
                     else THEME["amber"] if pct >= 80 else THEME["green"])
            canvas.coords(fill_id, 0, 0, int(width * frac), 5)
            canvas.itemconfigure(fill_id, fill=color)

        if "used" in u:
            self.lbl_used.config(text=f"limit modelu {u['used']}%")
            bar(self.bar_used, self.bar_used_fill, u["used"])
        if "plan" in u:
            self.lbl_plan.config(text=f"plan {u['plan']}%")
            bar(self.bar_plan, self.bar_plan_fill, u["plan"])
        if "reset" in u:
            self.lbl_reset_seen.config(
                text=f"zapowiedziany reset: {u['reset']:%a %d.%m %H:%M}")

    def _render_state(self):
        st = self.state_info["state"]
        running = st != "IDLE"
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")
        label, color_key = STATE_VIEW[st]
        self.lbl_state.config(text=label)
        self.lamp.itemconfigure(self.lamp_id, fill=THEME[color_key])

    def _tick_ui(self):
        st = self.state_info["state"]
        now = dt.datetime.now()
        t = THEME

        if st == "IDLE":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["muted"])
            self.lbl_caption.config(text="Kliknij „Rozpocznij czuwanie”.")
        elif st == "MONITORING":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["text"])
            session = self.usage.get("session")
            status = getattr(self, "last_status", "")
            if status.startswith("Nie widzę"):
                self.lbl_caption.config(text=status)
            elif session:
                self.lbl_caption.config(text=f"Pilnuję sesji: {session[:60]}")
            else:
                self.lbl_caption.config(
                    text=f"Skanuję okno Claude co {self.cfg['scan_interval_s']} s.")
        elif st == "ARMED":
            send_at = self.state_info.get("send_at")
            if send_at:
                secs = max(0, int((send_at - now).total_seconds()))
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                self.lbl_clock.config(text=f"{h:02d}:{m:02d}:{s:02d}", fg=t["amber"])
                reset_at = self.state_info.get("reset_at")
                if reset_at:
                    self.lbl_caption.config(
                        text=f"Reset {reset_at:%a %H:%M} — wyślę „continue” "
                             f"o {send_at:%H:%M:%S}.")
                else:
                    self.lbl_caption.config(
                        text=f"Nie znam godziny resetu — spróbuję "
                             f"o {send_at:%H:%M:%S}.")
            # lampka mruga w rytmie 1 Hz
            blink_on = int(time.time()) % 2 == 0
            self.lamp.itemconfigure(
                self.lamp_id, fill=t["amber"] if blink_on else t["amber_dim"])
        elif st == "VERIFY":
            self.lbl_clock.config(text=f"{now:%H:%M:%S}", fg=t["blue"])
            self.lbl_caption.config(text="Za chwilę sprawdzę, czy sesja ruszyła.")

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
    # ostre DPI na Windows 11
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
