# data.json / history.json contract (authoritative — builders #1 and #2 both obey this)

The fetcher writes `data.json` and `history.json` to the `data` branch. The
frontend reads `data.json` only (history is internal to the fetcher). All
numbers are plain JSON numbers; missing/unknown values are `null` (never a
string sentinel). All strings are already plain (frontend still escapes on render).

## data.json

```json
{
  "generated_at": "2026-07-16T21:40:05Z",      // UTC ISO8601, when this cycle finished
  "generated_at_ct": "2026-07-16 16:40 CT",     // human string, Central
  "session_date": "2026-07-16",                  // trading date this data belongs to
  "market_state": "closed",                      // "open" | "premarket" | "afterhours" | "closed"
  "universe": {
    "watched": 35,         // size of the curated pinned list (no market screen)
    "candidates": 35,      // of those, how many resolved a live quote
    "with_options": 35,    // of those, how many had a usable CBOE chain
    "pinned": 35           // len(PINNED) — watchlist + sector ETFs
  },
  "stats": {                 // header tiles (computed across BOTH boards' members, deduped by ticker)
    "bullish_flow": 12,
    "bearish_flow": 9,
    "firing": 3,
    "high_conviction": 14    // conviction-board members with score >= 60 (swing-only tickers are not counted)
  },
  "conviction": [ <ConvictionCard>, ... ],   // 0-7 DTE board, sorted score desc
  "swing": [ <SwingCard>, ... ],              // 14d-6mo board, sorted score desc
  "notes": {
    "flow_proxy": "Net flow = call premium traded minus put premium traded (volume x last x 100). Free data can't see buy/sell side — this is premium changing hands, not directional order flow.",
    "delay": "Options data is 15-minute delayed (CBOE free feed). Stock prices update live every 30s (TradingView Cboe One).",
    "tilt": "…methodology one-liner for the aggressor tilt (see build_snapshot.py header)…",
    "oi_confirm": "…methodology one-liner for OI-confirm…"
  }
}
```

> **Note on `notes`:** the frontend does NOT render `notes.*` — it ships its
> own tooltip copy (the `TIPS` object in `index.html`). The `notes` strings
> here and the `TIPS` text describe the same methodology and must be kept in
> sync whenever scoring or weights change.

### ConvictionCard
```json
{
  "ticker": "MU",
  "tv_symbol": "NASDAQ:MU",        // exchange-prefixed, for the browser's live TV poll
  "direction": "BULL",             // "BULL" | "BEAR"  (sign of 0-7DTE net flow)
  "firing": true,                  // score>=80 OR flow accel vs prior cycle
  "score": 87,                     // 0-100 int
  "spot": 858.35,                  // underlying price at fetch (CBOE current_price)
  "spot_at_alert": 851.10,         // spot when first appeared on this board today (from history); null if new-this-cycle
  "net_flow": 4250000.0,           // signed $, 0-7 DTE (call prem - put prem)
  "cp_ratio": 2.35,                // call vol / put vol, 0-7 DTE; null if no put vol
  "rvol": 1.04,                    // relative_volume_10d_calc from TV
  "change_pct": -0.66,             // TV change (day % )
  "tilt": 0.64,                    // aggressor tilt, -1..+1: day-accumulated sampled buy/sell
                                   // classification of traded contracts vs their bid/ask
                                   // (+1 = all classified premium leaned bullish); null until
                                   // anything classifies ("sampling"). Both DTE buckets.
  "tilt_prem": 1250000.0,          // $ premium classified into the tilt today (both sides summed)
  "popular_contract": {            // max-premium contract within +/-20% moneyness, 0-7 DTE; null if none
    "side": "CALL",                // "CALL" | "PUT"
    "strike": 860.0,
    "expiry": "2026-07-17",
    "dte": 1,
    "last": 12.40,
    "delta": 0.52,
    "iv": 0.98,                    // decimal (0.98 = 98%)
    "volume": 8200,
    "open_interest": 4100,
    "occ": "MU260717C00860000"
  }
}
```

### SwingCard
```json
{
  "ticker": "MU",
  "tv_symbol": "NASDAQ:MU",
  "direction": "BULL",
  "score": 72,                     // 0-100 int (swing-weighted; see scoring doc in build_snapshot.py)
  "spot": 858.35,
  "spot_at_alert": 851.10,         // null if new
  "persist": 4,                    // n out of 5 sessions same-direction net flow (from history)
  "persist_max": 5,
  "flow_5d": 18500000.0,           // signed $, sum of last up-to-5 sessions' net flow
  "flow_5d_pct": 62.0,             // flow_5d as % of gross premium (calls+puts) over the same sessions; signed, -100..+100; null if no gross history
  "oi_build": 12000,               // day-over-day sum-OI delta in the flow direction (contracts); null if <2 days history
  "oi_confirm": "OPENING",         // "OPENING" | "CLOSING" | "CHURN" | null — did yesterday's
                                   // swing-bucket (14-183d) volume become held OI (+/-25% of
                                   // yesterday's side volume)? null if <2 days of side data or
                                   // yesterday's side volume < 500
  "oi_confirm_frac": 0.41,         // (OI_today - OI_yest) / vol_yest on yesterday's direction side; null when oi_confirm is null
  "oi_confirm_side": "CALL",       // which side was checked (yesterday's direction); null when oi_confirm is null
  "trend": "UP",                   // "UP" | "DOWN" | "MIXED"  (spot vs SMA20/SMA50)
  "iv_rank": 63,                   // 0-100 percentile once >=20 sessions; else null
  "iv30": 0.98,                    // decimal; always present as fallback display
  "iv_collecting": true,           // true while <20 sessions of iv history (show "collecting history")
  "cp_skew": 1.85,                 // call prem / put prem, 14d-6mo; null if no put prem
  "earnings_in_window": true,      // TV earnings date falls inside suggested-contract expiry
  "earnings_days": 12,             // days to earnings; null if none/out of window
  "suggested_contract": {          // highest-premium 0.30-0.60 |delta|, 14d-6mo; null if none
    "side": "CALL",
    "strike": 900.0,
    "expiry": "2026-09-18",
    "dte": 64,
    "delta": 0.42,
    "iv": 0.95,
    "volume": 3100,
    "open_interest": 8800,
    "occ": "MU260918C00900000",
    "entry": 34.50,                // = last
    "stop": 24.15,                 // entry * 0.70  (-30%)
    "target": 70.73,               // entry * 2.05  (+105%)
    "rr": 3.5                      // fixed 3.5
  }
}
```

## history.json (fetcher-internal)
```json
{
  "sessions": {
    "2026-07-16": {
      "MU": {
        "net_flow_0_7": 4250000.0,   // signed
        "sum_oi_0_7": 210000,        // aggregate OI in the flow direction (contracts)
        "gross_prem_0_7": 10600000.0, // calls+puts premium, 0-7 DTE (denominator for flow_5d_pct)
        "iv30": 0.98,
        "direction": "BULL",
        "tilt_bull_prem": 2100000.0, // day-accumulated classified bullish premium (calls bought + puts sold)
        "tilt_bear_prem": 850000.0,  // day-accumulated classified bearish premium (calls sold + puts bought)
        "swing_vol_c": 41000,        // swing-bucket (14-183d) call volume   — OI-confirm inputs
        "swing_vol_p": 28000,        // swing-bucket put volume
        "swing_oi_c": 910000,        // swing-bucket call OI
        "swing_oi_p": 640000,        // swing-bucket put OI
        "first_board_conviction": {"time": "2026-07-16T14:32:00Z", "spot": 851.10},
        "first_board_swing": {"time": "2026-07-16T14:32:00Z", "spot": 851.10}
      }
    }
  },
  "iv_history": { "MU": [0.91, 0.88, 0.98, ...] }   // per-name daily iv30, most-recent last, for IV rank
}
```
Keep max 60 sessions; prune older. `iv_history` keeps max 60 values/name.
On each cycle: reload history, update today's row (net_flow, sum_oi, iv30, direction,
swing side vol/OI; tilt_*_prem ACCUMULATE across the day's cycles rather than being
overwritten), set first_board_* only if not already set today, recompute
persist/flow_5d/flow_5d_pct/oi_build/oi_confirm/iv_rank.

`fetcher/.prev_cycle.json` (job-local, gitignored, NOT part of the data branch):
`{"session": "2026-07-18", "flows": {ticker: net_flow}, "vols": {ticker: {occ: cum_volume}}}`.
flows drives the firing accel check; vols is the per-contract baseline for the
aggressor-tilt volume deltas (same session only — after a workflow restart the
first cycle contributes no tilt, by design). Legacy flat {ticker: net_flow}
files are still readable.

## Symbol hygiene (fetcher)
Skip TV tickers containing `/`, `.`, `-` (preferred shares, warrants, units).
Root for OCC = the plain ticker (strip exchange prefix). Skip CBOE 404s.
