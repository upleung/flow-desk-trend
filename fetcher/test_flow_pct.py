"""FLOW % (flow_pct / flow_side) — the near-money premium-weighted put/call split.

Run: python3 -m pytest fetcher/test_flow_pct.py

Why the near-money restriction exists (2026-07-28): premium is intrinsic +
extrinsic value, and a deep-ITM contract costs almost exactly what it is
already worth. A handful of those carry enormous "premium" while betting on
nothing — they are a way of holding the stock. Weighting the whole 0-7 DTE
bucket by dollars let that paper dictate the reading: LLY on 2026-07-27
printed 84% CALL off seven Jul-31 strikes ~35% below a ~$1,205 spot.

These tests pin the fix AND pin the thing the fix must not touch: net_flow
keeps its whole-bucket definition, because net_flow is a scoring input.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_snapshot import analyze_ticker, MONEYNESS_BAND  # noqa: E402

SESSION = date(2026, 7, 27)


def _occ(root: str, expiry: str, cp: str, strike: float) -> str:
    return f"{root}{expiry}{cp}{int(round(strike * 1000)):08d}"


def _opt(root, expiry, cp, strike, vol, last, oi=100, bid=None, ask=None):
    return {
        "option": _occ(root, expiry, cp, strike),
        "volume": vol, "last_trade_price": last, "open_interest": oi,
        "bid": bid if bid is not None else max(0.0, last - 0.05),
        "ask": ask if ask is not None else last + 0.05,
        "delta": 0.5, "iv": 0.4,
    }


def _chain(spot, options, iv30=0.4):
    return {"spot": spot, "iv30": iv30, "options": options}


# expiry 2 days after SESSION -> inside the 0-7 DTE bucket
EXP = "260729"


def test_deep_itm_stock_replacement_no_longer_sets_the_split():
    """The LLY shape, reduced: a wall of cheap near-money puts against a few
    very expensive deep-ITM calls. Whole-bucket dollars say CALL; the honest
    near-money read says PUT."""
    spot = 1200.0
    options = [
        # deep-ITM calls ~34% below spot: ~100% intrinsic, not a bet
        _opt("LLY", EXP, "C", 790.0, 64, 409.0),   # $2.62M
        _opt("LLY", EXP, "C", 795.0, 64, 404.0),   # $2.59M
        # real near-money book: puts outspend calls
        _opt("LLY", EXP, "C", 1200.0, 200, 18.0),  # $0.36M
        _opt("LLY", EXP, "P", 1190.0, 500, 20.0),  # $1.00M
    ]
    a = analyze_ticker("LLY", _chain(spot, options), SESSION)

    # near-money only: calls $0.36M vs puts $1.00M -> 73.5% PUT
    assert a["flow_side"] == "PUT"
    assert a["flow_pct"] == pytest.approx(73.5, abs=0.1)

    # net_flow is deliberately UNCHANGED — whole bucket, deep-ITM calls included
    assert a["net_flow"] == pytest.approx(
        (64 * 409.0 + 64 * 404.0 + 200 * 18.0 - 500 * 20.0) * 100)
    assert a["direction"] == "BULL"     # and it still reads BULL off that


def test_split_ignores_strikes_outside_the_band_on_both_sides():
    """Symmetry: deep-OTM lottery strikes and deep-ITM strikes are both out."""
    spot = 100.0
    far_call = 100.0 * (1 + MONEYNESS_BAND) + 1.0    # just outside
    far_put = 100.0 * (1 - MONEYNESS_BAND) - 1.0     # just outside
    options = [
        _opt("T", EXP, "C", far_call, 10_000, 0.02),   # deep OTM churn
        _opt("T", EXP, "P", far_put, 10_000, 0.02),
        _opt("T", EXP, "C", 40.0, 50, 60.0),          # deep ITM call
        _opt("T", EXP, "P", 160.0, 50, 60.0),         # deep ITM put
        _opt("T", EXP, "C", 100.0, 100, 2.0),         # the only real bets
        _opt("T", EXP, "P", 100.0, 300, 2.0),
    ]
    a = analyze_ticker("T", _chain(spot, options), SESSION)
    # $20k calls vs $60k puts -> 75% PUT
    assert a["flow_side"] == "PUT"
    assert a["flow_pct"] == pytest.approx(75.0, abs=0.1)


def test_strike_exactly_on_the_band_edge_is_included():
    spot = 100.0
    options = [
        _opt("T", EXP, "C", spot * (1 + MONEYNESS_BAND), 100, 1.0),
        _opt("T", EXP, "P", spot * (1 - MONEYNESS_BAND), 100, 3.0),
    ]
    a = analyze_ticker("T", _chain(spot, options), SESSION)
    assert a["flow_pct"] == pytest.approx(75.0, abs=0.1)
    assert a["flow_side"] == "PUT"


def test_no_near_money_premium_reports_nothing_rather_than_guessing():
    """All the volume is far from the money -> the split is unknown, and the
    card must show a dash instead of a number nobody can defend."""
    spot = 100.0
    options = [
        _opt("T", EXP, "C", 40.0, 500, 60.0),
        _opt("T", EXP, "P", 300.0, 500, 200.0),
    ]
    a = analyze_ticker("T", _chain(spot, options), SESSION)
    assert a["flow_pct"] is None and a["flow_side"] is None
    # but the raw bucket still reported, so net_flow/score are unaffected
    assert a["net_flow"] != 0


def test_missing_spot_fails_closed():
    """With no spot there is no way to tell a stock-replacement strike from a
    bet, so the split must be withheld, not guessed."""
    options = [_opt("T", EXP, "C", 100.0, 100, 1.0),
               _opt("T", EXP, "P", 100.0, 100, 3.0)]
    a = analyze_ticker("T", _chain(None, options), SESSION)
    assert a["flow_pct"] is None and a["flow_side"] is None


def test_split_is_always_the_dominant_side_between_50_and_100():
    spot = 100.0
    for cpx, ppx in ((1.0, 4.0), (4.0, 1.0), (2.0, 2.0), (0.05, 9.9)):
        options = [_opt("T", EXP, "C", 100.0, 500, cpx),
                   _opt("T", EXP, "P", 100.0, 500, ppx)]
        a = analyze_ticker("T", _chain(spot, options), SESSION)
        assert 50.0 <= a["flow_pct"] <= 100.0


def test_exact_tie_resolves_to_put():
    spot = 100.0
    options = [_opt("T", EXP, "C", 100.0, 100, 2.0),
               _opt("T", EXP, "P", 100.0, 100, 2.0)]
    a = analyze_ticker("T", _chain(spot, options), SESSION)
    assert a["flow_pct"] == pytest.approx(50.0)
    assert a["flow_side"] == "PUT"


def test_contracts_outside_the_dte_bucket_never_count():
    """FLOW % is a 0-7 DTE reading; a near-money 30-day contract is not it."""
    spot = 100.0
    options = [
        _opt("T", EXP, "C", 100.0, 100, 1.0),
        _opt("T", "260901", "P", 100.0, 10_000, 50.0),   # ~37 DTE, huge
    ]
    a = analyze_ticker("T", _chain(spot, options), SESSION)
    assert a["flow_side"] == "CALL"
    assert a["flow_pct"] == pytest.approx(100.0)
