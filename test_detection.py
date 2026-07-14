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

print()
if FAILURES:
    print(f"{len(FAILURES)} test(s) FAILED")
    raise SystemExit(1)
print("all tests passed")
