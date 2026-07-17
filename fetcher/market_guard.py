"""Market-hours guard for Flow Desk's fetch/publish loop.

Self-contained, stdlib only. Two windows are exposed:

  should_run(now)        — the STRICT CT trading session, 08:25-15:05 Mon-Fri.
                            Mirrors market-data/event-alerts/market_guard.py
                            exactly (same constants, same semantics) so the
                            "is the market open" answer stays consistent
                            across both repos.

  should_publish(now)     — the EXTENDED pre/post window loop.py actually
                            runs on: 08:00 CT (30min pre-market) through
                            15:20 CT (15min post-close), Mon-Fri. Options
                            chains and TV quotes are still meaningfully fresh
                            in that halo, so the publish loop uses this wider
                            window instead of the strict session.

Usage (workflow step):
    python3 market_guard.py >> $GITHUB_OUTPUT

Manual truth-table test:
    python3 market_guard.py --test
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

from zoneinfo import ZoneInfo

TZ_CT = ZoneInfo("America/Chicago")

# Strict session window (inclusive both ends) — matches event-alerts/market_guard.py
_OPEN_H,  _OPEN_M  = 8, 25
_CLOSE_H, _CLOSE_M = 15, 5

# Extended publish window — 30min pre-market through 15min post-close
_EXT_OPEN_H,  _EXT_OPEN_M  = 8, 0
_EXT_CLOSE_H, _EXT_CLOSE_M = 15, 20


def _minute_of_day(now: datetime) -> int:
    return now.hour * 60 + now.minute


def _in_window(now: datetime, open_h: int, open_m: int,
                close_h: int, close_m: int) -> bool:
    if now.weekday() >= 5:   # Sat=5, Sun=6
        return False
    open_min  = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    cur_min   = _minute_of_day(now)
    return open_min <= cur_min <= close_min


def _in_session(now: datetime) -> bool:
    """True if *now* is within the STRICT CT trading window, Mon-Fri."""
    return _in_window(now, _OPEN_H, _OPEN_M, _CLOSE_H, _CLOSE_M)


def in_extended_window(now: datetime) -> bool:
    """True if *now* is within the EXTENDED pre/post publish window, Mon-Fri."""
    return _in_window(now, _EXT_OPEN_H, _EXT_OPEN_M, _EXT_CLOSE_H, _EXT_CLOSE_M)


def should_run(now: datetime | None = None) -> bool:
    """Return True if the strict session guard passes this cycle."""
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    if now is None:
        now = datetime.now(tz=TZ_CT)
    return _in_session(now)


def should_publish(now: datetime | None = None) -> bool:
    """Return True if loop.py's extended publish window passes this cycle.

    This is the guard loop.py actually calls. workflow_dispatch always
    passes (manual/smoke-test runs), matching should_run's convention.
    """
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    if now is None:
        now = datetime.now(tz=TZ_CT)
    return in_extended_window(now)


if __name__ == "__main__":
    if "--test" in sys.argv:
        cases = [
            # (description, weekday, hour, minute, expect_strict, expect_extended)
            ("Mon 07:59 CT → pre-ext OUT",    0, 7,  59, False, False),
            ("Mon 08:00 CT → ext IN, strict OUT", 0, 8, 0, False, True),
            ("Mon 08:24 CT → ext IN, strict OUT", 0, 8, 24, False, True),
            ("Mon 08:25 CT → both IN",        0, 8,  25, True,  True),
            ("Mon 09:00 CT → both IN",        0, 9,  0,  True,  True),
            ("Mon 15:05 CT → both IN",        0, 15, 5,  True,  True),
            ("Mon 15:06 CT → ext IN, strict OUT", 0, 15, 6, False, True),
            ("Mon 15:20 CT → ext IN, strict OUT", 0, 15, 20, False, True),
            ("Mon 15:21 CT → both OUT",       0, 15, 21, False, False),
            ("Sat 10:00 CT → both OUT",       5, 10, 0,  False, False),
            ("Sun 10:00 CT → both OUT",       6, 10, 0,  False, False),
            ("Fri 14:59 CT → both IN",        4, 14, 59, True,  True),
        ]
        failed = 0
        base = date(2026, 6, 8)  # Monday
        for desc, wd, h, m, exp_strict, exp_ext in cases:
            delta = (wd - base.weekday()) % 7
            d = base.toordinal() + delta
            fake = datetime(*date.fromordinal(d).timetuple()[:3], h, m, 0,
                             tzinfo=TZ_CT)
            got_strict = _in_session(fake)
            got_ext = in_extended_window(fake)
            ok = (got_strict == exp_strict) and (got_ext == exp_ext)
            status = "OK" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"  {status}  {desc}: strict expected={exp_strict} got={got_strict} "
                  f"| extended expected={exp_ext} got={got_ext}")
        print(f"\n{len(cases) - failed}/{len(cases)} passed")
        sys.exit(0 if failed == 0 else 1)
    else:
        print(f"run={'true' if should_run() else 'false'}")
        print(f"publish={'true' if should_publish() else 'false'}")
