"""Flow Desk — one options-flow snapshot build cycle.

Free-data only, Python stdlib only (urllib/json/datetime/zoneinfo/math/os).
Every fetched field is type-checked before use; every step is fail-soft —
one bad name (a CBOE 404, a malformed TV row) is skipped and logged, it
never aborts the run. See /home/user/flow-desk/DATA_CONTRACT.md for the
authoritative output schema this module must produce.

────────────────────────────────────────────────────────────────────────────
PIPELINE (letters match the build spec)
────────────────────────────────────────────────────────────────────────────
(a) Two TradingView screens (close>=5, market_cap>=$500M, NASDAQ/NYSE/AMEX):
    top 150 by relative_volume_10d_calc desc, and the two "change" extremes
    (top 150 desc + top 150 asc) to catch big gainers AND big losers. Union.
(b) Union with a static ~65-name CORE_LIST (megacaps/semis/financials/
    energy/index ETFs/other liquid optionable names) plus the mandatory
    watchlist (MU, CRWD, COHR, LLY, V, XOM). Core names not already resolved
    by the screens are looked up via a SELF-HEALING exchange probe (try
    NASDAQ:, then NYSE:, then AMEX: batch quote calls) rather than a
    hand-maintained ticker->exchange dict — a live check during
    development found COHR listed on NYSE, not NASDAQ as an existing
    vault mapping assumed, so trusting one static dict here would have
    silently dropped a mandatory watchlist name.
(c) Pre-score = rvol * |change_pct|; take top MAX_CANDIDATES (40), always
    keeping every core watchlist name even outside the top 40.
(d) Fetch CBOE delayed chain per candidate (0.3s sleep, skip HTTP errors of
    any kind fail-soft — 403/404 both observed live for bad/optionless
    symbols). Parse OCC symbols (root+YYMMDD+C/P+strike*1000, 15 fixed-width
    chars from the end) into two DTE buckets:
      0-7 DTE  -> net_flow, cp_ratio, aggregate vol/OI, popular_contract,
                  conviction score (see WEIGHTS_CONVICTION below).
      14-183d  -> cp_skew, suggested_contract (0.30<=|delta|<=0.60, highest
                  premium), entry/stop/target/rr, earnings-in-window.
(e) Swing metrics pulled from history.json: persist (n/5 same-direction
    sessions), flow_5d, oi_build, trend (spot vs SMA20/SMA50), iv_rank
    (percentile once >=20 iv30 sessions collected).
(f) Swing score (see WEIGHTS_SWING below).
(g) Board membership: conviction = usable 0-7DTE bucket, score desc, top 24.
    swing = usable suggested_contract, swing-score desc, top 24.
(h) Alert memory: first_board_conviction/first_board_swing {time, spot} set
    once per ticker per day, read back from history.json.
(i) Header stats across the union of both boards, deduped by ticker.
(j) Write data.json + history.json (60 sessions / 60 iv values kept, older
    pruned).

────────────────────────────────────────────────────────────────────────────
CONVICTION SCORE (0-100 int) — weights sum to 100
────────────────────────────────────────────────────────────────────────────
  RVOL              25 pts  min(rvol / 3.0, 1.0) * 25            (3x rvol caps)
  Momentum          20 pts  min(|change_pct| / 5.0, 1.0) * 15
                            + 5 bonus if sign(change_pct) == sign(change_from_open)
                              (move is continuing through the day, not fading)
  Flow magnitude    25 pts  min(log10(|net_flow|+1) / 7.0, 1.0) * 20
                              (7 decades ~= $10M 0-7DTE premium caps the scale)
                            + 5 bonus if sign(net_flow) == sign(change_pct)
                              (options flow agrees with the stock's own move)
  C/P extremity     15 pts  min(|ln(cp_ratio)| / 2.0, 1.0) * 15
                              (symmetric around cp_ratio==1.0; 0 if no put vol)
  Vol/OI (0-7DTE)   10 pts  min((sum_vol/sum_oi) / 3.0, 1.0) * 10
  Contract concen.   5 pts  min(popular_contract premium / total 0-7 premium, 1.0) * 5

firing = score >= 80 OR net_flow accelerated vs the prior cycle: a name's
previous cycle's net_flow is cached in fetcher/.prev_cycle.json (fail-soft,
absent on first run); acceleration = same-sign net_flow with
|net_flow| >= |prev| * ACCEL_MULT (1.5x).

────────────────────────────────────────────────────────────────────────────
SWING SCORE (0-100 int) — weights sum to 100, persist dominates
────────────────────────────────────────────────────────────────────────────
  Persist           35 pts  persist / persist_max * 35   (heaviest — a flow
                              that repeats session after session is the
                              highest-conviction swing signal free data can
                              offer; single-day flow is noisy)
  Flow_5d magnitude 20 pts  min(log10(|flow_5d|+1) / 7.5, 1.0) * 20
  OI build          15 pts  min(oi_build / 20000, 1.0) * 15 if oi_build > 0
                              else 0 (only reward OI actually building; a
                              shrinking or unknown OI trend earns nothing)
  Trend alignment   15 pts  15 if trend matches direction (UP+BULL/DOWN+BEAR)
                            7  if trend == MIXED
                            0  if trend opposes direction
  IV rank           10 pts  10 * (1 - iv_rank/100) once available;
                              5 (neutral) while iv_collecting
                              (INVERTED: cheap vol scores higher — when you
                              buy weeks of premium, low IV rank is the edge)
  C/P skew          5 pts   min(|ln(cp_skew)| / 2.0, 1.0) * 5

All fields are documented in DATA_CONTRACT.md; this module must match it.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ_CT = ZoneInfo("America/Chicago")

ROOT = Path(__file__).resolve().parent            # flow-desk/fetcher/
DEFAULT_OUT_DIR = ROOT.parent / "data"

PREV_CYCLE_FILE = ROOT / ".prev_cycle.json"

UA = "Mozilla/5.0 (flow-desk)"
TIMEOUT = 20
CBOE_SLEEP_SEC = 0.3

TV_SCAN_URL = "https://scanner.tradingview.com/america/scan"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"

MAX_CANDIDATES = 40
SCREEN_RANGE = 150
BOARD_CAP = 24
MAX_HISTORY_SESSIONS = 60
MAX_IV_HISTORY = 60

ACCEL_MULT = 1.5             # net_flow acceleration threshold for "firing"
MONEYNESS_BAND = 0.20        # +/-20% of spot for 0-7DTE popular_contract
SWING_DELTA_LO, SWING_DELTA_HI = 0.30, 0.60
DTE_SHORT_LO, DTE_SHORT_HI = 0, 7
DTE_SWING_LO, DTE_SWING_HI = 14, 183

STOP_MULT = 0.70
TARGET_MULT = 2.05
FIXED_RR = 3.5


# ── Static core list (mandatory watchlist always included) ─────────────────
WATCHLIST = ["MU", "CRWD", "COHR", "LLY", "V", "XOM"]

# Best-effort documentation dict (used only as a first guess / for the
# report); actual TV symbol resolution self-heals via _resolve_core_tv()
# below, which probes NASDAQ -> NYSE -> AMEX live rather than trusting this.
CORE_EXCHANGE_GUESS: dict[str, str] = {
    # Megacaps
    "AAPL": "NASDAQ", "MSFT": "NASDAQ", "NVDA": "NASDAQ", "GOOGL": "NASDAQ",
    "AMZN": "NASDAQ", "META": "NASDAQ", "TSLA": "NASDAQ", "NFLX": "NASDAQ",
    "AVGO": "NASDAQ",
    # Semis
    "MU": "NASDAQ", "AMD": "NASDAQ", "INTC": "NASDAQ", "QCOM": "NASDAQ",
    "TXN": "NASDAQ", "ARM": "NASDAQ", "SMCI": "NASDAQ", "TSM": "NYSE",
    "ASML": "NASDAQ", "LRCX": "NASDAQ", "AMAT": "NASDAQ", "MRVL": "NASDAQ",
    # Financials
    "JPM": "NYSE", "BAC": "NYSE", "GS": "NYSE", "MS": "NYSE", "WFC": "NYSE",
    "C": "NYSE", "V": "NYSE", "MA": "NYSE", "AXP": "NYSE",
    # Energy
    "XOM": "NYSE", "CVX": "NYSE", "COP": "NYSE", "SLB": "NYSE", "OXY": "NYSE",
    # Index ETFs
    "SPY": "AMEX", "QQQ": "NASDAQ", "IWM": "AMEX", "DIA": "AMEX",
    # Other liquid optionable names
    "BABA": "NYSE", "PLTR": "NASDAQ", "COIN": "NASDAQ", "HOOD": "NASDAQ",
    "SOFI": "NASDAQ", "UBER": "NYSE", "DIS": "NYSE", "BA": "NYSE",
    "CAT": "NYSE", "GE": "NYSE", "WMT": "NYSE", "COST": "NASDAQ",
    "HD": "NYSE", "LLY": "NYSE", "UNH": "NYSE", "JNJ": "NYSE", "PFE": "NYSE",
    "KO": "NYSE", "PEP": "NASDAQ", "MCD": "NYSE", "DAL": "NYSE",
    "AAL": "NASDAQ", "F": "NYSE", "GM": "NYSE", "NKE": "NYSE",
    "SBUX": "NASDAQ", "CRWD": "NASDAQ", "PANW": "NASDAQ", "SNOW": "NYSE",
    # Watchlist-only addition not otherwise in the sector lists above
    "COHR": "NYSE",   # verified live 2026-07-16: NASDAQ:COHR returns 0 rows
}

CORE_LIST = sorted(set(CORE_EXCHANGE_GUESS) | set(WATCHLIST))

TV_COLUMNS = [
    "name", "close", "change", "change_from_open",
    "relative_volume_10d_calc", "market_cap_basic", "SMA20", "SMA50",
    "earnings_release_next_date",
]
# index positions into the "d" row, named for readability
_COL = {name: i for i, name in enumerate(TV_COLUMNS)}


class DataError(Exception):
    pass


def log(msg: str) -> None:
    print(f"[build_snapshot] {msg}")


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "text/plain", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def _is_clean_ticker(sym: str) -> bool:
    """Symbol hygiene: skip preferred shares / warrants / units."""
    return isinstance(sym, str) and sym and not any(c in sym for c in "/.-")


def _row_to_quote(sym_field: str, d: list) -> dict | None:
    """Validate one TV response row -> quote dict, or None on bad shape."""
    if not isinstance(d, list) or len(d) < len(TV_COLUMNS):
        return None
    if not isinstance(sym_field, str) or ":" not in sym_field:
        return None
    exch, ticker = sym_field.split(":", 1)
    if not _is_clean_ticker(ticker):
        return None

    def _num(i):
        v = d[i]
        return float(v) if isinstance(v, (int, float)) else None

    close = _num(_COL["close"])
    change = _num(_COL["change"])
    rvol = _num(_COL["relative_volume_10d_calc"])
    if close is None or change is None or rvol is None:
        return None
    earnings_raw = d[_COL["earnings_release_next_date"]]
    earnings_ts = int(earnings_raw) if isinstance(earnings_raw, (int, float)) else None
    return {
        "ticker": ticker,
        "tv_symbol": f"{exch}:{ticker}",
        "close": close,
        "change_pct": change,
        "change_from_open": _num(_COL["change_from_open"]),
        "rvol": rvol,
        "market_cap": _num(_COL["market_cap_basic"]),
        "sma20": _num(_COL["SMA20"]),
        "sma50": _num(_COL["SMA50"]),
        "earnings_ts": earnings_ts,
    }


def tv_screen(sort_order: str) -> dict[str, dict]:
    """One screen query (close>=5, mcap>=500M, NASDAQ/NYSE/AMEX). Fail-soft: {}."""
    body = {
        "columns": TV_COLUMNS,
        "filter": [
            {"left": "close", "operation": "greater", "right": 5},
            {"left": "market_cap_basic", "operation": "greater", "right": 500_000_000},
            {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
        ],
        "sort": {"sortBy": "relative_volume_10d_calc" if sort_order == "rvol"
                  else "change", "sortOrder": "desc" if sort_order != "change_asc" else "asc"},
        "range": [0, SCREEN_RANGE],
        "markets": ["america"],
    }
    try:
        raw = _post_json(TV_SCAN_URL, body)
    except Exception as e:
        log(f"WARN tv_screen({sort_order}) failed: {e}")
        return {}
    total = raw.get("totalCount")
    rows = raw.get("data")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        q = _row_to_quote(item.get("s"), item.get("d"))
        if q:
            out[q["ticker"]] = q
    out["__total__"] = total if isinstance(total, int) else len(out)
    return out


def _resolve_core_tv(missing: list[str]) -> dict[str, dict]:
    """Self-healing exchange probe for core tickers the screens didn't cover.

    Tries NASDAQ -> NYSE -> AMEX batch quote calls; a ticker only needs one
    successful match. Fail-soft per exchange call.
    """
    resolved: dict[str, dict] = {}
    remaining = list(missing)
    for exch in ("NASDAQ", "NYSE", "AMEX"):
        if not remaining:
            break
        tickers = [f"{exch}:{t}" for t in remaining]
        body = {"symbols": {"tickers": tickers}, "columns": TV_COLUMNS}
        try:
            raw = _post_json(TV_SCAN_URL, body)
        except Exception as e:
            log(f"WARN core resolve on {exch} failed: {e}")
            continue
        rows = raw.get("data")
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            q = _row_to_quote(item.get("s"), item.get("d"))
            if q and q["ticker"] not in resolved:
                resolved[q["ticker"]] = q
        remaining = [t for t in remaining if t not in resolved]
    if remaining:
        log(f"WARN could not resolve TV symbol for: {remaining}")
    return resolved


def build_universe(dry_run: bool = False) -> tuple[dict[str, dict], int]:
    """Union of TV screens + core list -> {ticker: quote}, screened count."""
    screened_total = 0
    quotes: dict[str, dict] = {}

    screen_specs = [("rvol", "desc"), ("change", "desc"), ("change", "asc")]
    for field, order in screen_specs:
        key = "rvol" if field == "rvol" else ("change_asc" if order == "asc" else "change")
        res = tv_screen(key)
        total = res.pop("__total__", 0)
        screened_total = max(screened_total, total if isinstance(total, int) else 0)
        for t, q in res.items():
            quotes.setdefault(t, q)

    missing_core = [t for t in CORE_LIST if t not in quotes]
    if missing_core:
        quotes.update(_resolve_core_tv(missing_core))

    if dry_run:
        # Keep it small & fast: watchlist + a handful of top-prescore names.
        keep = set(WATCHLIST)
        prescored = sorted(
            (t for t in quotes if t not in keep),
            key=lambda t: (quotes[t]["rvol"] or 0) * abs(quotes[t]["change_pct"] or 0),
            reverse=True,
        )
        keep |= set(prescored[:8])
        quotes = {t: q for t, q in quotes.items() if t in keep}

    return quotes, screened_total


def select_candidates(quotes: dict[str, dict]) -> list[str]:
    """Pre-score = rvol*|change|; top MAX_CANDIDATES, always keep watchlist."""
    scored = sorted(
        quotes.keys(),
        key=lambda t: (quotes[t]["rvol"] or 0) * abs(quotes[t]["change_pct"] or 0),
        reverse=True,
    )
    top = scored[:MAX_CANDIDATES]
    keep = list(dict.fromkeys(top + [t for t in WATCHLIST if t in quotes]))
    return keep


# ── CBOE chain fetch + OCC parsing ──────────────────────────────────────────

def fetch_chain(ticker: str) -> dict | None:
    """Fetch + minimally validate a CBOE delayed chain. None on any failure."""
    url = CBOE_URL.format(sym=ticker)
    try:
        raw = _get_json(url)
    except Exception as e:
        log(f"skip {ticker}: chain fetch failed ({e})")
        return None
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        log(f"skip {ticker}: unexpected chain shape")
        return None
    options = data.get("options")
    if not isinstance(options, list):
        log(f"skip {ticker}: no options list")
        return None
    spot = data.get("current_price")
    if not isinstance(spot, (int, float)) or spot <= 0:
        spot = data.get("close") if isinstance(data.get("close"), (int, float)) else None
    iv30 = data.get("iv30")
    # CBOE's top-level iv30 is a PERCENTAGE (98.469 == 98.469%, verified live
    # 2026-07-16 against MU/ASTS/AAPL) — divide by 100 for the contract's
    # decimal convention (0.98 == 98%). Per-contract "iv" is already decimal.
    iv30_decimal = (float(iv30) / 100.0) if isinstance(iv30, (int, float)) else None
    return {
        "spot": float(spot) if isinstance(spot, (int, float)) else None,
        "iv30": iv30_decimal,
        "options": options,
    }


def parse_occ(occ: str) -> tuple[str, str, str, float] | None:
    """OCC symbol -> (root, yymmdd, 'C'|'P', strike). None if malformed."""
    if not isinstance(occ, str) or len(occ) < 15:
        return None
    tail = occ[-15:]
    root = occ[:-15]
    yymmdd, cp, strike_str = tail[0:6], tail[6], tail[7:15]
    if cp not in ("C", "P") or not yymmdd.isdigit() or not strike_str.isdigit():
        return None
    try:
        strike = int(strike_str) / 1000.0
    except ValueError:
        return None
    return root, yymmdd, cp, strike


def _expiry_date(yymmdd: str) -> date | None:
    try:
        yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def analyze_ticker(ticker: str, chain: dict, session_date: date) -> dict:
    """Bucket a chain's contracts into 0-7DTE and 14-183d groups + metrics."""
    spot = chain["spot"]
    short_calls_vol = short_puts_vol = 0.0
    short_calls_prem = short_puts_prem = 0.0
    short_calls_oi = short_puts_oi = 0.0
    popular = None       # (premium, contract_dict)
    swing_candidates: list[tuple[float, dict]] = []  # (premium, contract) 0.30<=|delta|<=0.60
    swing_call_prem = swing_put_prem = 0.0

    for opt in chain["options"]:
        if not isinstance(opt, dict):
            continue
        occ = opt.get("option")
        parsed = parse_occ(occ) if isinstance(occ, str) else None
        if not parsed:
            continue
        root, yymmdd, cp, strike = parsed
        expiry = _expiry_date(yymmdd)
        if expiry is None:
            continue
        dte = (expiry - session_date).days
        if dte < 0:
            continue

        vol = _num(opt.get("volume")) or 0.0
        oi = _num(opt.get("open_interest")) or 0.0
        last = _num(opt.get("last_trade_price")) or 0.0
        delta = _num(opt.get("delta"))
        iv = _num(opt.get("iv"))
        premium = vol * last * 100.0

        if DTE_SHORT_LO <= dte <= DTE_SHORT_HI:
            if cp == "C":
                short_calls_vol += vol
                short_calls_prem += premium
                short_calls_oi += oi
            else:
                short_puts_vol += vol
                short_puts_prem += premium
                short_puts_oi += oi

            if spot and spot > 0 and abs(strike / spot - 1) <= MONEYNESS_BAND:
                if popular is None or premium > popular[0]:
                    popular = (premium, {
                        "side": "CALL" if cp == "C" else "PUT",
                        "strike": strike,
                        "expiry": expiry.isoformat(),
                        "dte": dte,
                        "last": last,
                        "delta": delta,
                        "iv": iv,
                        "volume": int(vol),
                        "open_interest": int(oi),
                        "occ": occ,
                    })

        if DTE_SWING_LO <= dte <= DTE_SWING_HI:
            if cp == "C":
                swing_call_prem += premium
            else:
                swing_put_prem += premium
            if delta is not None and SWING_DELTA_LO <= abs(delta) <= SWING_DELTA_HI:
                swing_candidates.append((premium, {
                    "side": "CALL" if cp == "C" else "PUT",
                    "strike": strike,
                    "expiry": expiry.isoformat(),
                    "dte": dte,
                    "delta": delta,
                    "iv": iv,
                    "volume": int(vol),
                    "open_interest": int(oi),
                    "occ": occ,
                    "entry": last,
                }))

    net_flow = short_calls_prem - short_puts_prem
    cp_ratio = (short_calls_vol / short_puts_vol) if short_puts_vol > 0 else None
    sum_vol_0_7 = short_calls_vol + short_puts_vol
    sum_oi_0_7_total = short_calls_oi + short_puts_oi
    direction = "BULL" if net_flow >= 0 else "BEAR"
    # OI tracked "in the flow direction" — the side matching today's direction
    sum_oi_directional = short_calls_oi if direction == "BULL" else short_puts_oi

    cp_skew = (swing_call_prem / swing_put_prem) if swing_put_prem > 0 else None
    suggested = None
    if swing_candidates:
        swing_candidates.sort(key=lambda x: x[0], reverse=True)
        # The suggested contract must express the card's thesis: a BULL card
        # suggests a CALL, a BEAR card suggests a PUT. Pick the highest-premium
        # candidate on the matching side; only if that side has none do we fall
        # back to the highest-premium candidate overall.
        want_side = "CALL" if direction == "BULL" else "PUT"
        matching = [t for t in swing_candidates if t[1]["side"] == want_side]
        prem, c = (matching[0] if matching else swing_candidates[0])
        entry = c.pop("entry")
        c["entry"] = entry
        c["stop"] = round(entry * STOP_MULT, 2) if entry else None
        c["target"] = round(entry * TARGET_MULT, 2) if entry else None
        c["rr"] = FIXED_RR
        suggested = c

    return {
        "spot": spot,
        "iv30": chain["iv30"],
        "direction": direction,
        "net_flow": net_flow,
        "cp_ratio": cp_ratio,
        "sum_vol_0_7": sum_vol_0_7,
        "sum_oi_0_7_total": sum_oi_0_7_total,
        "sum_oi_0_7_directional": sum_oi_directional,
        "popular_contract": popular[1] if popular else None,
        "popular_premium": popular[0] if popular else 0.0,
        "total_premium_0_7": short_calls_prem + short_puts_prem,
        "has_short_bucket": sum_vol_0_7 > 0,
        "cp_skew": cp_skew,
        "suggested_contract": suggested,
    }


# ── History (persistence across cycles) ─────────────────────────────────────

def load_history(out_dir: Path) -> dict:
    path = out_dir / "history.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("not a dict")
        raw.setdefault("sessions", {})
        raw.setdefault("iv_history", {})
        return raw
    except Exception:
        return {"sessions": {}, "iv_history": {}}


def save_history(out_dir: Path, history: dict) -> None:
    sessions = history.get("sessions", {})
    if len(sessions) > MAX_HISTORY_SESSIONS:
        for k in sorted(sessions.keys())[:-MAX_HISTORY_SESSIONS]:
            del sessions[k]
    for t, vals in history.get("iv_history", {}).items():
        if isinstance(vals, list) and len(vals) > MAX_IV_HISTORY:
            history["iv_history"][t] = vals[-MAX_IV_HISTORY:]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "history.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_prev_cycle() -> dict:
    try:
        raw = json.loads(PREV_CYCLE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def save_prev_cycle(data: dict) -> None:
    try:
        PREV_CYCLE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        log(f"WARN could not write prev-cycle cache: {e}")


# ── Scoring ──────────────────────────────────────────────────────────────────

def conviction_score(analysis: dict, quote: dict) -> int:
    rvol = quote["rvol"] or 0.0
    change_pct = quote["change_pct"] or 0.0
    change_from_open = quote["change_from_open"]
    net_flow = analysis["net_flow"]
    cp_ratio = analysis["cp_ratio"]
    sum_vol = analysis["sum_vol_0_7"]
    sum_oi = analysis["sum_oi_0_7_total"]
    total_prem = analysis["total_premium_0_7"]
    popular_prem = analysis["popular_premium"]

    pts = 0.0
    pts += min(rvol / 3.0, 1.0) * 25

    mom = min(abs(change_pct) / 5.0, 1.0) * 15
    if change_from_open is not None and change_pct != 0 and (
            (change_pct > 0) == (change_from_open > 0)):
        mom += 5
    pts += mom

    flow = min(math.log10(abs(net_flow) + 1) / 7.0, 1.0) * 20
    if net_flow != 0 and change_pct != 0 and ((net_flow > 0) == (change_pct > 0)):
        flow += 5
    pts += flow

    if cp_ratio and cp_ratio > 0:
        dist = min(abs(math.log(cp_ratio)) / 2.0, 1.0)
        pts += dist * 15

    if sum_oi > 0:
        pts += min((sum_vol / sum_oi) / 3.0, 1.0) * 10

    if total_prem > 0:
        pts += min(popular_prem / total_prem, 1.0) * 5

    return max(0, min(100, round(pts)))


def swing_score(persist: int, persist_max: int, flow_5d: float,
                oi_build: float | None, trend: str, direction: str,
                iv_rank: float | None, cp_skew: float | None) -> int:
    pts = 0.0
    if persist_max > 0:
        pts += (persist / persist_max) * 35

    pts += min(math.log10(abs(flow_5d) + 1) / 7.5, 1.0) * 20

    if oi_build is not None and oi_build > 0:
        pts += min(oi_build / 20000.0, 1.0) * 15

    if (trend == "UP" and direction == "BULL") or (trend == "DOWN" and direction == "BEAR"):
        pts += 15
    elif trend == "MIXED":
        pts += 7

    if iv_rank is None:
        pts += 5
    else:
        pts += max(0.0, 10 * (1 - iv_rank / 100))

    if cp_skew and cp_skew > 0:
        pts += min(abs(math.log(cp_skew)) / 2.0, 1.0) * 5

    return max(0, min(100, round(pts)))


# ── Main cycle ───────────────────────────────────────────────────────────────

def run_cycle(out_dir: Path, dry_run: bool = False) -> dict:
    now_utc = datetime.now(tz=timezone.utc)
    now_ct = now_utc.astimezone(TZ_CT)
    session_date = now_ct.date()
    session_str = session_date.isoformat()

    log(f"cycle start {now_ct.strftime('%Y-%m-%d %H:%M CT')}")

    quotes, screened = build_universe(dry_run=dry_run)
    log(f"screened={screened} universe={len(quotes)}")

    candidates = select_candidates(quotes)
    log(f"candidates={len(candidates)}")

    history = load_history(out_dir)
    today_sessions = history["sessions"].setdefault(session_str, {})
    iv_history = history["iv_history"]
    prev_cycle = load_prev_cycle()
    new_prev_cycle: dict[str, float] = {}

    conviction_cards = []
    swing_cards = []
    with_options = 0

    for i, ticker in enumerate(candidates):
        if i > 0:
            time.sleep(CBOE_SLEEP_SEC)
        chain = fetch_chain(ticker)
        if chain is None:
            continue
        with_options += 1
        quote = quotes.get(ticker)
        if quote is None:
            continue

        analysis = analyze_ticker(ticker, chain, session_date)
        direction = analysis["direction"]
        net_flow = analysis["net_flow"]
        new_prev_cycle[ticker] = net_flow

        # iv30 history
        if analysis["iv30"] is not None:
            iv_history.setdefault(ticker, []).append(analysis["iv30"])

        # Today's session row (persisted regardless of board membership).
        # first_board_* fields, if already set earlier this same day (e.g. a
        # prior cycle), must survive this reassignment — carried forward
        # explicitly since first_board_* is stamped in a later pass below.
        prior_today = today_sessions.get(ticker)
        today_sessions[ticker] = {
            "net_flow_0_7": net_flow,
            "sum_oi_0_7": analysis["sum_oi_0_7_directional"],
            "iv30": analysis["iv30"],
            "direction": direction,
        }
        if isinstance(prior_today, dict):
            for k in ("first_board_conviction", "first_board_swing"):
                if k in prior_today and prior_today[k]:
                    today_sessions[ticker][k] = prior_today[k]

        # ── swing metrics from history ──────────────────────────────────
        # persist: n/5 -- fixed 5-session denominator (contract's PERSIST_MAX);
        # missing prior sessions simply don't count as hits (not penalized
        # beyond the fraction they represent).
        prior_dates = sorted(d for d in history["sessions"].keys() if d < session_str)
        last5_dates = prior_dates[-5:]
        persist = 0
        for d in last5_dates:
            row = history["sessions"][d].get(ticker)
            if isinstance(row, dict) and row.get("direction") == direction:
                persist += 1
        persist_max = 5

        # flow_5d = today's net_flow + up to 4 prior sessions' net_flow (5 total)
        flow_5d = net_flow
        for d in (last5_dates[-4:] if len(last5_dates) > 4 else last5_dates):
            row = history["sessions"][d].get(ticker)
            if isinstance(row, dict) and isinstance(row.get("net_flow_0_7"), (int, float)):
                flow_5d += row["net_flow_0_7"]

        # oi_build: today's directional sum_oi minus yesterday's
        oi_build = None
        if prior_dates:
            y_row = history["sessions"][prior_dates[-1]].get(ticker)
            if isinstance(y_row, dict) and isinstance(y_row.get("sum_oi_0_7"), (int, float)):
                oi_build = analysis["sum_oi_0_7_directional"] - y_row["sum_oi_0_7"]

        # trend: spot vs SMA20/SMA50
        spot = analysis["spot"]
        sma20, sma50 = quote.get("sma20"), quote.get("sma50")
        if spot and sma20 and sma50:
            if spot > sma20 and spot > sma50:
                trend = "UP"
            elif spot < sma20 and spot < sma50:
                trend = "DOWN"
            else:
                trend = "MIXED"
        else:
            trend = "MIXED"

        # iv_rank: percentile within iv_history (including today's just-appended value)
        ivs = iv_history.get(ticker, [])
        iv_rank = None
        iv_collecting = True
        if len(ivs) >= 20 and analysis["iv30"] is not None:
            iv_collecting = False
            sorted_ivs = sorted(ivs)
            rank_pos = sum(1 for v in sorted_ivs if v <= analysis["iv30"])
            iv_rank = round(100 * rank_pos / len(sorted_ivs))

        # earnings in window (needs suggested_contract expiry)
        suggested = analysis["suggested_contract"]
        earnings_ts = quote.get("earnings_ts")
        # earnings_days is only populated when earnings_in_window is true —
        # "null if none/out of window" per DATA_CONTRACT.md.
        earnings_in_window = False
        earnings_days = None
        if earnings_ts is not None and suggested is not None:
            try:
                edt = datetime.fromtimestamp(earnings_ts, tz=timezone.utc).date()
                expiry_d = date.fromisoformat(suggested["expiry"])
                if session_date <= edt <= expiry_d:
                    earnings_in_window = True
                    earnings_days = (edt - session_date).days
            except Exception:
                pass

        # Score + assemble candidate cards. spot_at_alert/first_board_* are
        # NOT stamped here — alert memory means "first time this ticker
        # actually appeared on the (capped, sorted) board", not merely "had
        # a usable bucket". Board membership is only decided after every
        # candidate's score is known and the list is sorted+capped below, so
        # stamping happens in a second pass over the surviving cards.
        conv_score = conviction_score(analysis, quote)
        accel = False
        prev_flow = prev_cycle.get(ticker)
        if isinstance(prev_flow, (int, float)) and prev_flow != 0 and net_flow != 0:
            if (net_flow > 0) == (prev_flow > 0) and abs(net_flow) >= abs(prev_flow) * ACCEL_MULT:
                accel = True
        firing = conv_score >= 80 or accel

        has_short_bucket = analysis["has_short_bucket"] or analysis["popular_contract"] is not None
        if has_short_bucket:
            conviction_cards.append({
                "ticker": ticker,
                "tv_symbol": quote["tv_symbol"],
                "direction": direction,
                "firing": firing,
                "score": conv_score,
                "spot": spot,
                "net_flow": net_flow,
                "cp_ratio": analysis["cp_ratio"],
                "rvol": quote["rvol"],
                "change_pct": quote["change_pct"],
                "popular_contract": analysis["popular_contract"],
            })

        if suggested is not None:
            sw_score = swing_score(persist, persist_max, flow_5d, oi_build,
                                    trend, direction, iv_rank, analysis["cp_skew"])
            swing_cards.append({
                "ticker": ticker,
                "tv_symbol": quote["tv_symbol"],
                "direction": direction,
                "score": sw_score,
                "spot": spot,
                "persist": persist,
                "persist_max": persist_max,
                "flow_5d": flow_5d,
                "oi_build": oi_build,
                "trend": trend,
                "iv_rank": iv_rank,
                "iv30": analysis["iv30"],
                "iv_collecting": iv_collecting,
                "cp_skew": analysis["cp_skew"],
                "earnings_in_window": earnings_in_window,
                "earnings_days": earnings_days,
                "suggested_contract": suggested,
            })

    conviction_cards.sort(key=lambda c: c["score"], reverse=True)
    swing_cards.sort(key=lambda c: c["score"], reverse=True)
    conviction_cards = conviction_cards[:BOARD_CAP]
    swing_cards = swing_cards[:BOARD_CAP]

    # ── alert memory: stamp first_board_* only for names that actually made
    # the capped board this cycle, and only once per ticker per day ────────
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for board_cards, key in ((conviction_cards, "first_board_conviction"),
                              (swing_cards, "first_board_swing")):
        for c in board_cards:
            ticker = c["ticker"]
            row = today_sessions.setdefault(ticker, {})
            if key not in row or not row[key]:
                row[key] = {"time": now_iso, "spot": c["spot"]}
            fb = row[key]
            c["spot_at_alert"] = fb.get("spot") if isinstance(fb, dict) else None

    # ── stats tiles (union of both boards, deduped) ─────────────────────────
    by_ticker: dict[str, dict] = {}
    for c in conviction_cards:
        by_ticker[c["ticker"]] = {"direction": c["direction"], "firing": c["firing"],
                                   "score_conv": c["score"]}
    for c in swing_cards:
        e = by_ticker.setdefault(c["ticker"], {"direction": c["direction"], "firing": False,
                                                "score_conv": None})
        e.setdefault("direction", c["direction"])

    bullish_flow = sum(1 for v in by_ticker.values() if v["direction"] == "BULL")
    bearish_flow = sum(1 for v in by_ticker.values() if v["direction"] == "BEAR")
    firing_count = sum(1 for v in by_ticker.values() if v.get("firing"))
    high_conviction = sum(1 for v in by_ticker.values()
                          if v.get("score_conv") is not None and v["score_conv"] >= 60)

    if now_ct.weekday() >= 5:
        market_state = "closed"
    else:
        open_min = 8 * 60 + 30
        close_min = 15 * 60
        cur_min = now_ct.hour * 60 + now_ct.minute
        pre_min = 8 * 60
        post_min = 15 * 60 + 20
        if open_min <= cur_min <= close_min:
            market_state = "open"
        elif pre_min <= cur_min < open_min:
            market_state = "premarket"
        elif close_min < cur_min <= post_min:
            market_state = "afterhours"
        else:
            market_state = "closed"

    data = {
        "generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_ct": now_ct.strftime("%Y-%m-%d %H:%M CT"),
        "session_date": session_str,
        "market_state": market_state,
        "universe": {
            "screened": screened,
            "candidates": len(candidates),
            "with_options": with_options,
            "core_list": len(CORE_LIST),
        },
        "stats": {
            "bullish_flow": bullish_flow,
            "bearish_flow": bearish_flow,
            "firing": firing_count,
            "high_conviction": high_conviction,
        },
        "conviction": conviction_cards,
        "swing": swing_cards,
        "notes": {
            "flow_proxy": ("Net flow = call premium traded minus put premium traded "
                           "(volume x last x 100). Free data can't see buy/sell side "
                           "— this is premium changing hands, not directional order flow."),
            "delay": ("Options data is 15-minute delayed (CBOE free feed). Stock prices "
                      "update live every 30s (TradingView Cboe One)."),
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / "data.json"
    tmp = data_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(data_path)

    save_history(out_dir, history)
    save_prev_cycle(new_prev_cycle)

    log(f"wrote {data_path} ({data_path.stat().st_size} bytes)")
    return data


def _print_summary(data: dict) -> None:
    u = data["universe"]
    print(f"\nscreened={u['screened']}  candidates={u['candidates']}  "
          f"with_options={u['with_options']}  core_list={u['core_list']}")
    print(f"stats: {data['stats']}")
    print("\nTop 5 conviction:")
    for c in data["conviction"][:5]:
        print(f"  {c['ticker']:<6} score={c['score']:<3} dir={c['direction']:<4} "
              f"net_flow={c['net_flow']:,.0f} firing={c['firing']}")
    print("\nTop 5 swing:")
    for c in data["swing"][:5]:
        print(f"  {c['ticker']:<6} score={c['score']:<3} dir={c['direction']:<4} "
              f"persist={c['persist']}/{c['persist_max']} flow_5d={c['flow_5d']:,.0f}")


def main() -> int:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    out_dir_env = os.environ.get("OUT_DIR")
    out_dir = Path(out_dir_env) if out_dir_env else DEFAULT_OUT_DIR
    try:
        data = run_cycle(out_dir, dry_run=dry_run)
    except Exception as e:
        import traceback
        print(f"[build_snapshot] FATAL: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1
    _print_summary(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
