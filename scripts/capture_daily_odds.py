"""Capture today's NBA closing lines from The Odds API *live* endpoint (~1 credit).

The free, going-forward complement to the one-time historical backfill: the live
`/v4/sports/.../odds` endpoint costs ~1 credit/call (vs 10 for historical) and
returns the current slate's lines. Run it near tip-off (cron) and it applies
near-closing consensus moneylines to today's `events`. Idempotent — running it
again later in the evening refreshes later games to their actual closing line.

At ~1-3 credits/day it sits inside the free 500/mo tier, so ongoing odds cost
~$0. In the offseason there are no games and it just spends ~1 credit, applies 0.

    python scripts/capture_daily_odds.py            # apply today's lines
    python scripts/capture_daily_odds.py --min-credits 50   # skip if quota low
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.config import load_settings  # noqa: E402
from sportsball.db import Database  # noqa: E402
from sportsball.logging_conf import get_logger  # noqa: E402
from sportsball.store import Store  # noqa: E402

# Reuse the ET-localized parser so live lines match nba_api's ET game dates.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_odds_history import parse_snapshot_et  # noqa: E402

log = get_logger("capture_daily_odds")
LIVE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"


def _key() -> str:
    return os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("ODDS_API_KEY=")), "")


def main() -> None:
    p = argparse.ArgumentParser(description="Capture today's NBA closing lines (live, ~1 credit)")
    p.add_argument("--min-credits", type=int, default=10,
                   help="skip the call if remaining quota is below this")
    args = p.parse_args()

    key = _key()
    if not key or "your_" in key:
        log.error("ODDS_API_KEY not set — skipping."); return

    try:
        r = requests.get(LIVE_URL, params={
            "apiKey": key, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"}, timeout=20)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.error("Odds API fetch failed: %s", exc); return

    remaining = int(r.headers.get("x-requests-remaining", -1))
    if 0 <= remaining < args.min_credits:
        log.warning("Quota low (%s < %s) — skipping apply.", remaining, args.min_credits); return

    rows = parse_snapshot_et(r.json())
    store = Store(Database(load_settings().db))
    known = {row[0] for row in store.db.query(
        "SELECT event_id FROM events WHERE event_date >= now() - interval '2 days'")}
    applied = 0
    for eid, hc, ac in rows:
        if eid in known:
            store.update_closing_odds(eid, hc, ac)
            applied += 1
    log.info("Captured %d priced games; applied %d to events (credits left ~%s, spent ~%s).",
             len(rows), applied, remaining, r.headers.get("x-requests-last", "1"))


if __name__ == "__main__":
    main()
