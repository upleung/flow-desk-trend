"""Flow Desk publish loop: market-guarded, runs build_snapshot cycles, pushes
data.json + history.json to the `data` branch as a single squashed commit.

Usage:
  python3 loop.py                # normal guarded loop (for the workflow)
  python3 loop.py --force         # bypass the guard for exactly one cycle, then loop normally
  FORCE_ONE_CYCLE=1 python3 loop.py   # same as --force, via env (workflow smoke-test)

Env:
  OUT_DIR           path to the data-branch checkout (default: ../data next to
                    this file, same default build_snapshot.py uses standalone)
  LOOP_SLEEP_SEC    seconds between cycles (default 420)
  MAX_RUN_SEC       redispatch threshold (default 19800 = 5.5h)

Design notes:
- Guard: uses market_guard.should_publish() — the EXTENDED pre/post window
  (08:00-15:20 CT), not the strict trading session, per the build spec.
- Push strategy: force-push a single squashed commit each cycle
  (git checkout --orphan tmp -> commit -> branch -M data -> push -f) so the
  data branch never accumulates history bloat across a whole trading day of
  ~50 cycles. The remote + auth are configured by the calling workflow;
  loop.py only runs git subprocess commands.
- Safe to run locally with no git repo / no push access at all: every git
  step is wrapped and warns-and-continues rather than crashing, so the
  compute path (build_snapshot) can be exercised standalone.
- Long-running safety: GitHub Actions job timeouts are typically 6h. If
  elapsed time approaches MAX_RUN_SEC (5.5h) and the market is still in the
  publish window, this prints "REDISPATCH" and exits 0 so the calling
  workflow can re-trigger itself for the rest of the session.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from market_guard import should_publish, TZ_CT
import build_snapshot

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = ROOT.parent / "data"

LOOP_SLEEP_SEC = int(os.environ.get("LOOP_SLEEP_SEC", "3600"))
MAX_RUN_SEC = int(os.environ.get("MAX_RUN_SEC", str(19800)))  # 5.5h
GIT_RETRIES = 3
GIT_RETRY_BACKOFF_SEC = 5


def log(msg: str) -> None:
    print(f"[loop] {msg}")


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess | None:
    """Run one git command in cwd. Returns CompletedProcess, or None on failure."""
    try:
        return subprocess.run(
            ["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        log(f"WARN git {' '.join(args)} raised: {e}")
        return None


def _is_git_repo(out_dir: Path) -> bool:
    res = _run_git(["rev-parse", "--is-inside-work-tree"], out_dir)
    return bool(res and res.returncode == 0 and res.stdout.strip() == "true")


def publish(out_dir: Path) -> bool:
    """Force-push a single squashed commit of data.json/history.json to the
    `data` branch. Fail-soft — logs a warning and returns False on any
    problem (no git repo, no remote, no network, no push access).
    """
    if not _is_git_repo(out_dir):
        log(f"WARN {out_dir} is not a git repo — skipping publish (compute-only mode)")
        return False

    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for attempt in range(1, GIT_RETRIES + 1):
        ok = True
        steps = [
            ["checkout", "--orphan", f"_flowdesk_tmp_{int(time.time())}"],
            ["add", "-A"],
            ["commit", "-m", f"data {ts}"],
        ]
        tmp_branch = steps[0][2]
        for step in steps:
            res = _run_git(step, out_dir)
            if res is None or res.returncode != 0:
                stderr = res.stderr.strip() if res else "(no result)"
                log(f"WARN git {' '.join(step)} failed: {stderr}")
                ok = False
                break
        if ok:
            res = _run_git(["branch", "-M", "data"], out_dir)
            ok = bool(res and res.returncode == 0)
        if ok:
            res = _run_git(["push", "-f", "origin", "data"], out_dir)
            ok = bool(res and res.returncode == 0)
            if not ok:
                stderr = res.stderr.strip() if res else "(no result)"
                log(f"WARN git push failed: {stderr}")

        if ok:
            log(f"published to data branch (attempt {attempt})")
            return True

        if attempt < GIT_RETRIES:
            backoff = GIT_RETRY_BACKOFF_SEC * attempt
            log(f"publish attempt {attempt} failed — retrying in {backoff}s")
            time.sleep(backoff)

    log("ERROR: all publish attempts failed — continuing loop without publishing this cycle")
    return False


def run_one_cycle(out_dir: Path) -> None:
    """Run build_snapshot + publish, fail-soft (never raises)."""
    try:
        build_snapshot.run_cycle(out_dir, dry_run=False)
    except Exception as e:
        import traceback
        log(f"ERROR: build_snapshot cycle failed: {e}")
        traceback.print_exc()
        return
    try:
        publish(out_dir)
    except Exception as e:
        log(f"ERROR: publish step raised unexpectedly: {e}")


def main() -> int:
    args = sys.argv[1:]
    force_flag = "--force" in args
    force_env = os.environ.get("FORCE_ONE_CYCLE") == "1"
    force_one_cycle = force_flag or force_env

    out_dir_env = os.environ.get("OUT_DIR")
    out_dir = Path(out_dir_env) if out_dir_env else DEFAULT_OUT_DIR

    start = time.monotonic()
    first_cycle = True

    while True:
        now_ct = datetime.now(tz=TZ_CT)
        guarded_in = should_publish(now_ct)
        bypass = first_cycle and force_one_cycle

        if not guarded_in and not bypass:
            log(f"outside publish window ({now_ct.strftime('%H:%M CT %a')}) — exit")
            return 0

        if bypass:
            log("FORCE_ONE_CYCLE / --force: bypassing guard for exactly one cycle")

        run_one_cycle(out_dir)
        first_cycle = False

        if force_one_cycle:
            log("forced single cycle complete — exiting")
            return 0

        elapsed = time.monotonic() - start
        if elapsed >= MAX_RUN_SEC:
            # Only worth redispatching if the market's still in the publish window
            still_in = should_publish(datetime.now(tz=TZ_CT))
            if still_in:
                print("REDISPATCH")
                log(f"elapsed {elapsed:.0f}s >= MAX_RUN_SEC — signaling redispatch")
            return 0

        log(f"sleeping {LOOP_SLEEP_SEC}s...")
        time.sleep(LOOP_SLEEP_SEC)


if __name__ == "__main__":
    sys.exit(main())
