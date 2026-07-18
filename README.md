# Flow Desk

## What this is

Flow Desk is a personal, live options-flow dashboard. It watches the stock
market during trading hours, looks for unusual options activity, and shows
it on two simple boards. It uses only free data sources — there are no logins,
no API keys to manage, and nothing to pay for.

## The URL

**https://zlanghamer1.github.io/flow-desk/**

Open that link any time. It updates itself — there's nothing to install or run.

## How the data flows

1. A scheduled job (a "GitHub Action") wakes up roughly every 7 minutes while
   the market is open.
2. Each time it wakes up, it looks at a fixed, curated watch list — your
   watchlist names plus the 11 sector-index ETFs (XLE, XLC, XLP, and the
   rest) — and pulls free, 15-minute-delayed options data from CBOE and live
   stock prices from TradingView for each one. It no longer screens the whole
   market, so the boards only ever show names you chose to watch.
3. It scores each stock onto two boards:
   - **Conviction** — short-term activity, expiring in the next 0–7 days.
   - **Swing** — longer-term activity, expiring anywhere from 2 weeks to 6 months out.
4. The results are saved to a file called `data.json` and published to a
   separate branch of this repository (the `data` branch).
5. The website reads that file to draw the boards, and separately checks
   TradingView every 30 seconds so the prices on screen stay live even
   between refreshes.

## The two direction estimators (added 2026-07-18)

Free data can't directly see whether a trade was a buy or a sell, but two
things get squeezed out of the same free feed to get closer:

- **AGGR TILT (conviction board)** — every refresh, each contract that traded
  is checked against its bid/ask spread: a trade printed near the ask counts
  as *bought*, near the bid as *sold*. Calls bought + puts sold = bullish
  premium; calls sold + puts bought = bearish. The tilt is the day's running
  balance, −100% to +100%. It only samples one trade per contract per refresh
  (~7 min), so it's a rough proxy — the card shows how many dollars it's
  based on, and shows "sampling" until enough trades classify.
- **OI-confirm (swing board, under OI BUILD)** — the next morning, yesterday's
  longer-dated volume is checked against today's open interest. If open
  interest grew by at least 25% of that volume, yesterday's flow became held
  positions (**OPENING ✓** — conviction). If it shrank that much, positions
  were being unwound (**CLOSING ✗**). Anything between is **CHURN** —
  day-traded or rolled, not held.

Both nudge the 0–100 scores a little (tilt: ±5 on conviction; OI-confirm: +5
or −10 on swing) but never dominate them.

## Its limits

- **Options data is 15 minutes delayed.** It's free CBOE data, not a live feed.
- **"Net flow" is a proxy, not real order flow.** Free data can't tell you
  whether a trade was a buyer or a seller — it only shows how much option
  premium changed hands and in which direction the volume leaned. Treat it as
  a clue, not a fact. The AGGR TILT estimator above narrows this gap but is
  itself a sampled approximation, not the real tape.
- **This is not financial advice.** It's a personal research tool. Nothing on
  the boards is a recommendation to buy or sell anything.

## How to restart the loop if it stops

The refresh loop is designed to keep itself running all day on its own, but if
it ever stalls or you want to check on it:

1. Go to this repository on GitHub.
2. Click the **Actions** tab.
3. Click **"Refresh Loop"** in the left sidebar.
4. Click the **Run workflow** button (top right of the list) and confirm.
   - If you're testing this after the market has closed, tick the **force**
     checkbox first — that tells it to run one cycle anyway instead of
     waiting for market hours.

## What each file is

- **`index.html`** — the website itself. This is the whole dashboard.
- **`fetcher/`** — the behind-the-scenes scripts that pull data, score it,
  and publish it. You never need to open or run these by hand.
- **`.github/workflows/refresh-loop.yml`** — the schedule that keeps the
  data flowing during market hours.
- **`.github/workflows/pages.yml`** — the schedule that publishes the
  website itself whenever it changes.
- **`data.json`** — the live snapshot the website reads. You won't see this
  file on the `main` branch — it only lives on the `data` branch, since it's
  regenerated constantly and doesn't need a history of its own on `main`.
- **`history.json`** — the fetcher's day-over-day memory (per-name flow, open
  interest, IV history — what powers persistence, OI-confirm and IV rank). Like
  `data.json` it lives only on the `data` branch, not on `main`.
