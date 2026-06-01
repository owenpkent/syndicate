"""Backfill closing odds from The Odds API *historical* snapshots -> events.

The live endpoint only returns upcoming games; deep history needs the historical
snapshots endpoint (`/v4/historical/...`), which costs ~10 credits per call and
returns every game priced at a given timestamp. We query **one snapshot per real
game-day** (only days that still lack odds), at the slate's early-evening tip, so
the lines are near-closing consensus across US books.

Matching fix: The Odds API `commence_time` is UTC, which rolls evening US games
to the next calendar day; our `events` are keyed by the **ET game date**. We
localize commence_time to America/New_York before building the canonical id.

Credit-safe: pass --budget (max credits to spend) and --limit (max game-days);
it stops cleanly and is idempotent, so re-runs resume.

    python scripts/backfill_odds_history.py --since 2022-07-01 --limit 3   # test (~30 credits)
    python scripts/backfill_odds_history.py --since 2022-07-01             # full backfill
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.config import load_settings  # noqa: E402
from sportsball.db import Database  # noqa: E402
from sportsball.matching import canonical_event_id  # noqa: E402
from sportsball.pipelines.ingest_odds import _to_decimal, passes_vig_guard  # noqa: E402
from sportsball.store import Store  # noqa: E402
from statistics import median  # noqa: E402

ET = ZoneInfo("America/New_York")
HIST_URL = "https://api.the-odds-api.com/v4/historical/sports/basketball_nba/odds"


def parse_snapshot_et(snapshot: list[dict]) -> list[tuple]:
    """[(event_id, home_close, away_close)] with commence_time localized to ET."""
    out = []
    for ev in snapshot:
        home, away, when = ev.get("home_team"), ev.get("away_team"), ev.get("commence_time")
        if not (home and away and when):
            continue
        # UTC -> ET date so the id matches nba_api's ET game date.
        dt = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(ET)
        et_date = dt.date().isoformat()
        prices = {home: [], away: []}
        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for oc in market.get("outcomes", []):
                    dec = _to_decimal(oc.get("price"))
                    if dec is not None and oc.get("name") in prices:
                        prices[oc["name"]].append(dec)
        if not prices[home] or not prices[away]:
            continue
        hc, ac = round(median(prices[home]), 4), round(median(prices[away]), 4)
        if not passes_vig_guard(hc, ac):
            continue
        out.append((canonical_event_id("nba", et_date, away, home), hc, ac))
    return out


def fetch_snapshot(api_key: str, ts_iso: str) -> tuple[list[dict], int, int]:
    """Return (events, credits_spent_this_call, remaining)."""
    r = requests.get(HIST_URL, params={
        "apiKey": api_key, "regions": "us", "markets": "h2h",
        "oddsFormat": "decimal", "date": ts_iso}, timeout=25)
    r.raise_for_status()
    last = int(r.headers.get("x-requests-last", 10))
    remaining = int(r.headers.get("x-requests-remaining", -1))
    return r.json().get("data", []), last, remaining


def game_days_needing_odds(store: Store, since: str) -> list[str]:
    rows = store.db.query(
        "SELECT DISTINCT event_date FROM events "
        "WHERE event_date >= %s AND home_close IS NULL AND home_score IS NOT NULL "
        "ORDER BY event_date ASC", (since,))
    return [r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10] for r in rows]


def known_event_ids(store: Store, since: str) -> set[str]:
    rows = store.db.query(
        "SELECT event_id FROM events WHERE event_date >= %s AND home_score IS NOT NULL",
        (since,))
    return {r[0] for r in rows}


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill closing odds from Odds API history")
    p.add_argument("--since", default="2022-07-01", help="earliest ET game date to backfill")
    p.add_argument("--limit", type=int, default=None, help="max game-days (for testing)")
    p.add_argument("--budget", type=int, default=15000, help="max credits to spend")
    p.add_argument("--sleep", type=float, default=0.3)
    args = p.parse_args()

    import os
    key = os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("ODDS_API_KEY=")), "")
    if not key or "your_" in key:
        print("ODDS_API_KEY not set in env/.env"); return

    store = Store(Database(load_settings().db))
    known = known_event_ids(store, args.since)
    days = game_days_needing_odds(store, args.since)
    if args.limit:
        days = days[:args.limit]
    print(f"{len(days)} game-days to backfill (~{len(days)*10} credits, budget {args.budget}).")

    spent = applied = matched_days = 0
    i = 0
    for i, d in enumerate(days, 1):
        # Snapshot at the ET evening (D+1 00:00 UTC ≈ 7pm ET, near first tips).
        ts = (datetime.fromisoformat(d).replace(tzinfo=timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%dT00:00:00Z")
        try:
            snap, last, remaining = fetch_snapshot(key, ts)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {d}: fetch failed ({exc})"); continue
        spent += last
        rows = parse_snapshot_et(snap)
        n = 0
        for eid, hc, ac in rows:
            if eid in known:
                store.update_closing_odds(eid, hc, ac)
                n += 1
        applied += n
        if n:
            matched_days += 1
        if i % 25 == 0 or i == len(days):
            print(f"  [{i}/{len(days)}] {d}: {len(rows)} priced, {n} matched | "
                  f"spent {spent}, remaining {remaining}", flush=True)
        if spent >= args.budget:
            print(f"  budget {args.budget} reached — stopping at {d}."); break
        time.sleep(args.sleep)

    print("-" * 50)
    print(f"Spent ~{spent} credits over {i} days; applied {applied} closing lines "
          f"({matched_days} days matched).")


if __name__ == "__main__":
    main()
