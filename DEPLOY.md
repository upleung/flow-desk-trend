# Deploying Flow Desk

Plain-English steps to get the site live and keep it updating. Most of this is
already done — the remaining human step is noted at the bottom.

## Status

- [x] **Public repo created** — https://github.com/zlanghamer1/flow-desk (done by Zach).
- [x] **Everything built + committed locally** — `main` (site + `fetcher/` + workflows,
      committed on top of the repo's initial commit) and an orphan **`data`** branch
      (real `data.json` + `history.json` from a live fetch on 2026-07-16). Ready to push
      unchanged.
- [x] **Pushed to GitHub** — done 2026-07-16 evening (Claude session, after Zach
      granted the GitHub App access). `main` + `data` pushed; the ClaudeVault mirror
      at `market-data/flow-desk/repo/` remains the durable backup.
- [x] **Pages "on" + first refresh-loop run** — done 2026-07-16 evening. Site live,
      first forced refresh cycle published real data to the `data` branch.
      NOTE — deploy method changed during deploy: Pages runs in **branch mode from
      `gh-pages`** (the workflow token couldn't create a Pages site, and the
      auto-created github-pages environment rejected `actions/deploy-pages` runs
      from `main`). `pages.yml` now just syncs `index.html` from `main` onto
      `gh-pages`; GitHub's built-in "pages build and deployment" publishes it.
      Don't flip Settings → Pages to "GitHub Actions" — branch mode is intentional.

## The URL (once Pages finishes its first deploy)

**https://zlanghamer1.github.io/flow-desk/**

## How it stays live (no babysitting)

- `.github/workflows/pages.yml` runs automatically whenever `index.html` changes on
  `main`. It turns GitHub Pages on by itself (`configure-pages` with
  `enablement: true`) and publishes the site. Nothing to click.
- `.github/workflows/refresh-loop.yml` is the data engine. During market hours it
  runs a cycle every ~7 minutes: it scans the market, pulls free CBOE delayed option
  chains + TradingView prices, scores names into the two boards, and force-pushes a
  fresh `data.json` to the `data` branch. Before GitHub's 6-hour job limit it
  re-launches itself so it covers the whole trading day untouched. Two backup
  starters (a daily schedule at ~8:20am CT) exist in case the chain ever breaks.
- The website itself, in your browser, also polls TradingView every 30 seconds for
  live prices — so prices and "gain since alert" move in real time even between the
  ~7-minute flow refreshes.

## One step left (for a human)

None — deployed 2026-07-16. Kept for history: the step was granting the Claude
GitHub App access to this repo, then telling a Claude session "deploy flow-desk".

If Pages ever gets turned off: Settings → Pages → source **Deploy from a branch** →
branch **gh-pages** (root). (Branch mode, not "GitHub Actions" — see Status note.)

## If the refresh loop ever stops

Same as step 3: **Actions → Refresh Loop → Run workflow** (tick **force** to run one
cycle right now, or leave it unticked during market hours to start the normal loop).

## Restarting from a Claude session

Just tell Claude "deploy flow-desk" — the git remote is the standard proxy URL and the
push commands are the normal `git push origin main` / `git push origin data`. Claude
can also kick the workflow via the GitHub tools.

## Honest limits (also shown on the site)

- Options data is **15-minute delayed** (free CBOE feed).
- "Net flow" is a **premium-traded proxy** = call premium traded − put premium traded.
  Free data can't see who is buying vs selling — it's premium changing hands, not
  directional order flow.
- No penny stocks (price ≥ $5, market cap ≥ $500M). Informational only, not advice.
