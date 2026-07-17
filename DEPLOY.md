# Deploying Flow Desk

Plain-English steps to get the site live and keep it updating. Most of this is
already done — the remaining human step is noted at the bottom.

## Status

- [x] **Public repo created** — https://github.com/zlanghamer1/flow-desk (done by Zach).
- [x] **Everything built + committed locally** — `main` (site + `fetcher/` + workflows,
      committed on top of the repo's initial commit) and an orphan **`data`** branch
      (real `data.json` + `history.json` from a live fetch on 2026-07-16). Ready to push
      unchanged.
- [ ] **Pushed to GitHub** — BLOCKED at build time: the Claude session that built this
      only had access to the `claudevault` repo, so `git push` to `flow-desk` returned
      403. **This clears the moment the Claude GitHub App is granted access to
      `flow-desk`** (see "One step left"). A durable copy of everything lives in
      ClaudeVault at `market-data/flow-desk/repo/` in the meantime.
- [ ] **Pages "on" + first refresh-loop run** — happens automatically after the push.

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

**Grant the Claude GitHub App access to this repo**, then a Claude session pushes it.

1. Open https://github.com/settings/installations → the Claude / Claude Code app →
   **Configure** → under "Repository access" add **flow-desk** (or select "All
   repositories") → Save. (This is the same grant GitHub prompts for when you first
   point Claude at a new repo.)
2. Tell any Claude session: **"deploy flow-desk"** — it will `git push origin main`
   and `git push origin data`. (Or push from your own machine: clone this repo, copy
   in these files, `git push`.)
3. The **Deploy Pages** workflow runs automatically on the `main` push and turns Pages
   on. The **Refresh Loop** starts the data engine.

To light it up immediately after the push (instead of waiting for market open):

1. Go to the repo's **Actions** tab: https://github.com/zlanghamer1/flow-desk/actions
2. If GitHub asks you to **enable workflows** on this repo, click the button to enable them.
3. Open **"Refresh Loop"** in the left list → **Run workflow** → tick the **force**
   box → **Run workflow**. `force` makes it run one cycle immediately even though the
   market is closed, so you can confirm the `data` branch updates and the site shows data.
4. The **"Deploy Pages"** workflow should already have run from the `main` push. If the
   site 404s, open "Deploy Pages" → **Run workflow** once. Then visit the URL above.

If Pages isn't turned on automatically, go to **Settings → Pages** and set the source
to **GitHub Actions** (not "Deploy from a branch").

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
