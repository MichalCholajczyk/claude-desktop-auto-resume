# -*- coding: utf-8 -*-
"""Unit tests for the pure detection helpers (no UI Automation needed).

Run:  py test_detection.py
"""

import datetime as dt

from claude_auto_continue import detect_limit_banner, parse_reset_time

NOW = dt.datetime(2026, 7, 14, 13, 22, 0)
FAILURES = []


def check(label, cond, got=None):
    if cond:
        print(f"  ok  {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL  {label}  (got: {got!r})")


# --- 1. composer banner, elements split like the real UIA tree -------------
texts = [
    "Fable 5 is the most capable model and draws down usage much faster than Opus 4.8",
    "Usage limit reached",
    "Resets at 2:40 PM",
    "Upgrade",
    "Type / for commands",
    "Bypass permissions",
    "Fable 5",
    "High",
]
phrase, reset = detect_limit_banner(texts, now=NOW)
check("banner detected (split elements)", phrase is not None, phrase)
check("reset parsed 14:40 (split elements)",
      reset == dt.datetime(2026, 7, 14, 14, 40), reset)

# --- 2. banner and reset in a single element --------------------------------
phrase, reset = detect_limit_banner(
    ["Usage limit reached  Resets at 2:40 PM"], now=NOW)
check("banner detected (single element)", phrase is not None, phrase)
check("reset parsed 14:40 (single element)",
      reset == dt.datetime(2026, 7, 14, 14, 40), reset)

# --- 3. notification card without a reset time ------------------------------
phrase, reset = detect_limit_banner(
    ["Usage limit reached",
     "You’ve reached your usage limit. Try again after your limit resets.",
     "View details", "Try again"], now=NOW)
check("card detected", phrase is not None, phrase)
check("card has no reset time", reset is None, reset)

# --- 4. no false positive on the usage meter / panel texts ------------------
phrase, _ = detect_limit_banner(
    ["Usage: 5-hour limit 97%, resets in 1 hr 17 min",
     "Plan usage limits · Pro", "5-hour limit", "Resets in 1 hr 17 min",
     "97%", "Weekly · all models", "18%"], now=NOW)
check("meter/panel texts do not trigger", phrase is None, phrase)

# --- 5. ordinary UI texts do not trigger -------------------------------------
phrase, _ = detect_limit_banner(
    ["New chat", "Search", "Type / for commands", "Fable 5", "High",
     "Superpowers training app improvements"], now=NOW)
check("ordinary texts do not trigger", phrase is None, phrase)

# --- 6. Polish variants -------------------------------------------------------
phrase, reset = detect_limit_banner(
    ["Osiągnięto limit użycia", "Resetuje się o 14:40"], now=NOW)
check("polish banner detected", phrase is not None, phrase)
check("polish reset parsed 14:40",
      reset == dt.datetime(2026, 7, 14, 14, 40), reset)

# --- 7. parse_reset_time regression: 'Resets at 2:40 PM' ---------------------
check("parse_reset_time('Resets at 2:40 PM')",
      parse_reset_time("Resets at 2:40 PM", now=NOW)
      == dt.datetime(2026, 7, 14, 14, 40),
      parse_reset_time("Resets at 2:40 PM", now=NOW))

# --- 8. current Claude Code wording (15.09: reset 09:30 was never parsed) ------
TUE = dt.datetime(2026, 9, 15, 8, 50)


def local(zone, *args):
    from zoneinfo import ZoneInfo
    return dt.datetime(*args, tzinfo=ZoneInfo(zone)).astimezone().replace(tzinfo=None)


for label, text, expected in (
        ("CLI session-limit message", "You've hit your session limit · resets 9:30am (Europe/Warsaw)",
         local("Europe/Warsaw", 2026, 9, 15, 9, 30)),
        ("CLI message in another time zone", "You've hit your session limit · resets 9:30am (America/New_York)",
         local("America/New_York", 2026, 9, 15, 9, 30)),
        ("card hint with time", "Your session limit resets at 9:30 AM. Try again then.",
         dt.datetime(2026, 9, 15, 9, 30)),
        ("wait-until notice", "You’re out of usage credits. Buy more to keep going now, "
         "or wait until 9:30 AM when your plan usage resets.", dt.datetime(2026, 9, 15, 9, 30)),
        ("weekday in meter label", "Usage: Context 205.1k, Weekly · all models: 19%, Resets Mon 6:00 PM",
         dt.datetime(2026, 9, 21, 18, 0)),
        ("long weekday with 'at'", "You’ve reached your weekly limit. It resets Monday at 6:00 PM.",
         dt.datetime(2026, 9, 21, 18, 0)),
        ("time then weekday", "Or, wait until your session limit resets at 6:00 PM on Thursday to keep working.",
         dt.datetime(2026, 9, 17, 18, 0)),
        ("minutes:seconds countdown", "Resets in 4:30", TUE + dt.timedelta(minutes=4, seconds=30)),
        ("hours and minutes", "Resets in 2 hr 15 min", TUE + dt.timedelta(hours=2, minutes=15))):
    got = parse_reset_time(text, now=TUE)
    check(f"parse: {label}", got == expected, got)

# A reset that already passed today is stale, not tomorrow's (a sticky card read
# after the reset must not push the send 24 hours out) ...
got = parse_reset_time("Resets at 9:30 AM", now=dt.datetime(2026, 9, 15, 19, 40))
check("past same-day reset stays today", got == dt.datetime(2026, 9, 15, 9, 30), got)
# ... while a 5-hour window crossing midnight still resolves to tomorrow.
got = parse_reset_time("Resets at 12:30 AM", now=dt.datetime(2026, 9, 15, 23, 50))
check("reset after midnight is tomorrow", got == dt.datetime(2026, 9, 16, 0, 30), got)
got = parse_reset_time("Usage: Context 12k, Weekly · all models: 19%", now=TUE)
check("meter without reset gives None", got is None, got)

for text in ("Session limit reached", "Weekly limit reached", "Reached your session limit",
             "You’ve reached your session limit. It resets at 9:30 AM.",
             "You've hit your session limit · resets 9:30am (Europe/Warsaw)",
             "You hit your usage limit. Held messages will send when it resets.",
             "You’re out of usage credits. Buy more to keep going now, or wait until 9:30 AM when your plan usage resets."):
    phrase, _ = detect_limit_banner([text], now=TUE)
    check(f"notice detected: {text[:40]}", phrase is not None, phrase)
phrase, reset = detect_limit_banner(
    ["You’re out of usage credits. Buy more to keep going now, or wait until 9:30 AM when your plan usage resets."],
    now=TUE)
check("wait-until notice gives its reset", reset == dt.datetime(2026, 9, 15, 9, 30), reset)
for text in ("Approaching session limit", "You’ve used 90% of your session limit",
             "90% of your session limit", "Usage, Weekly · all models: 19%, Resets Mon 6:00 PM",
             "Usage-limit notices now say which limit you hit and when it resets"):
    phrase, _ = detect_limit_banner([text], now=TUE)
    check(f"not a notice: {text[:40]}", phrase is None, phrase)

print()
if FAILURES:
    print(f"{len(FAILURES)} test(s) FAILED")
    raise SystemExit(1)
print("all tests passed")
