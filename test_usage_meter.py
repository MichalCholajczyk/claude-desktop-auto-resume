# -*- coding: utf-8 -*-
"""Tests for _is_usage_meter — the guard that stopped the app clicking the
"Usage limit reached" notice (and switching the visible chat) instead of the
bottom-bar usage meter. Rects below are the real values captured live from
the window where the bug happened (handle 67336, rect 1273,0 .. 3847,1399).

Run:  py test_usage_meter.py
"""
import types

from claude_auto_continue import MonitorWorker

is_meter = MonitorWorker._is_usage_meter
FAILURES = []


def R(l, t, r, b):
    return types.SimpleNamespace(left=l, top=t, right=r, bottom=b)


WIN = R(1273, 0, 3847, 1399)   # the live window from the bug report


def check(label, cond):
    if cond:
        print(f"  ok  {label}")
    else:
        FAILURES.append(label)
        print(f"FAIL  {label}")


# 1. the REAL meter — tiny ButtonControl in the bottom bar -> accepted
check("real meter accepted",
      is_meter("Usage: context 21%, plan 90%", "ButtonControl",
               R(3068, 1358, 3088, 1378), WIN) is True)

# 2. the "Usage limit reached" notice — wide button, off-screen top -> rejected
check("off-screen notice rejected",
      is_meter("Usage limit reached", "ButtonControl",
               R(2320, -344, 2490, -323), WIN) is False)

# 3. a session titled "Usage…" in the header/sidebar (TextControl) -> rejected
check("session-title TextControl rejected",
      is_meter("Usage limit notification handling", "TextControl",
               R(1327, 282, 1523, 299), WIN) is False)

# 4. worst case: an inline "Usage limit reached" strip ABOVE the composer,
#    i.e. a WIDE button near the bottom — rejected on width, not just position
check("wide inline notice near bottom rejected",
      is_meter("Usage limit reached  Resets at 2:40 PM", "ButtonControl",
               R(1400, 1330, 1700, 1351), WIN) is False)

# 5. a small "usage"-ish button but high up (not the bottom bar) -> rejected
check("small button outside bottom band rejected",
      is_meter("Usage details", "ButtonControl",
               R(3060, 200, 3080, 220), WIN) is False)

# 6. missing geometry -> refuse (never click blindly)
check("no rect -> rejected",
      is_meter("Usage: context 5%, plan 5%", "ButtonControl", None, WIN) is False)
check("no win_rect -> rejected",
      is_meter("Usage: context 5%, plan 5%", "ButtonControl",
               R(3068, 1358, 3088, 1378), None) is False)

# 7. meter on a smaller window still accepted (band has a 120px floor)
SMALL = R(0, 0, 700, 660)
check("meter on small window accepted",
      is_meter("Usage: context 5%, plan 5%", "ButtonControl",
               R(660, 632, 680, 652), SMALL) is True)

print()
if FAILURES:
    print(f"{len(FAILURES)} test(s) FAILED")
    raise SystemExit(1)
print("all tests passed")
