"""Backfill HISTORICAL intraday per-book snapshots -> odds_snapshots (lead-lag).

The one thing money buys that time would otherwise give: paid historical snapshots
at many timestamps per game-day, so we can reconstruct each book's line-vs-time and
detect lead-lag (which book moves first) NOW instead of waiting weeks for the live
capture. Writes to the SAME odds_snapshots table the live capture uses.

The Odds API historical endpoint costs ~10 credits / market / call; h2h+totals = 20.
Use --dry-run first to see the exact credit cost, and --budget as a hard cap.

    # estimate cost only (no spend):
    python scripts/backfill_intraday_history.py --since 2026-04-01 --days 40 --dry-run
    # real pull (needs a paid ODDS_API_KEY):
    python scripts/backfill_intraday_history.py --since 2026-04-01 --days 40 --budget 18000
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.matching import canonical_event_id  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_odds_quotes import SPORTS, ET  # noqa: E402
from backfill_odds_markets_duckdb import DEFAULT_DB  # noqa: E402

HIST = "https://api.the-odds-api.com/v4/historical/sports/{sport_key}/odds"


def _key():
    return os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("ODDS_API_KEY=")), "")


def timestamps(since, days, win_start, win_end, interval):
    """UTC ISO timestamps: each day from `since`, every `interval` min over the ET
    window [win_start, win_end) (24h ET clock)."""
    d0 = datetime.fromisoformat(since).replace(tzinfo=ET)
    for dd in range(days):
        day = d0 + timedelta(days=dd)
        t = day.replace(hour=win_start, minute=0, second=0, microsecond=0)
        end = day.replace(hour=win_end, minute=0, second=0, microsecond=0)
        while t < end:
            yield t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            t += timedelta(minutes=interval)


def parse(data, prefix, captured):
    out = []
    for ev in data:
        home, away, when = ev.get("home_team"), ev.get("away_team"), ev.get("commence_time")
        if not (home and away and when):
            continue
        commence = datetime.fromisoformat(when.replace("Z", "+00:00"))
        eid = canonical_event_id(prefix, commence.astimezone(ET).date().isoformat(), away, home)
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                if m.get("key") not in ("h2h", "totals"):
                    continue
                for oc in m.get("outcomes", []):
                    if oc.get("price") is None:
                        continue
                    pt = oc.get("point")
                    out.append((eid, prefix, m["key"], bk.get("key"), oc.get("name"),
                                float(pt) if pt is not None else None, float(oc["price"]),
                                commence, captured))
    return out


def main():
    p = argparse.ArgumentParser(description="Historical intraday per-book snapshots -> odds_snapshots")
    p.add_argument("--sport-key", default="baseball_mlb")
    p.add_argument("--since", required=True, help="first ET date, YYYY-MM-DD")
    p.add_argument("--days", type=int, default=40)
    p.add_argument("--win-start", type=int, default=15, help="ET hour window start (24h)")
    p.add_argument("--win-end", type=int, default=23, help="ET hour window end (24h)")
    p.add_argument("--interval", type=int, default=20, help="minutes between snapshots")
    p.add_argument("--markets", default="h2h,totals")
    p.add_argument("--budget", type=int, default=18000, help="hard credit cap")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sleep", type=float, default=0.25)
    args = p.parse_args()

    prefix = SPORTS.get(args.sport_key, args.sport_key.split("_")[-1])
    ts_list = list(timestamps(args.since, args.days, args.win_start, args.win_end, args.interval))
    per_call = 10 * len(args.markets.split(","))
    est = len(ts_list) * per_call
    snaps_day = len(ts_list) // max(args.days, 1)
    print(f"{args.days} days x {snaps_day} snaps/day = {len(ts_list)} calls x {per_call} cr "
          f"= ~{est} credits ({args.markets}, {args.interval}-min, ET {args.win_start}:00-{args.win_end}:00).")
    if args.dry_run:
        print("dry-run: no API calls made."); return

    key = _key()
    if not key or "your_" in key:
        print("ODDS_API_KEY not set (paid key needed for historical)."); return
    con = duckdb.connect(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
        event_id TEXT, sport TEXT, market TEXT, bookmaker TEXT, side TEXT,
        point DOUBLE, price DOUBLE, commence_time TIMESTAMP, captured_at TIMESTAMP,
        PRIMARY KEY (event_id, market, bookmaker, side, captured_at));""")

    spent = wrote = 0
    for i, ts in enumerate(ts_list, 1):
        try:
            r = requests.get(HIST.format(sport_key=args.sport_key), params={
                "apiKey": key, "regions": "us", "markets": args.markets,
                "oddsFormat": "decimal", "date": ts}, timeout=30)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {ts}: {exc}"); continue
        spent += int(r.headers.get("x-requests-last", per_call))
        body = r.json()
        captured = datetime.fromisoformat(body.get("timestamp", ts).replace("Z", "+00:00"))
        rows = parse(body.get("data", []), prefix, captured)
        con.executemany(
            "INSERT INTO odds_snapshots VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (event_id, market, bookmaker, side, captured_at) DO NOTHING;", rows)
        wrote += len(rows)
        if i % 25 == 0 or i == len(ts_list):
            print(f"  [{i}/{len(ts_list)}] spent {spent}, remaining {r.headers.get('x-requests-remaining','?')}, "
                  f"{wrote} rows", flush=True)
        if spent >= args.budget:
            print(f"  budget {args.budget} reached."); break
        time.sleep(args.sleep)
    total = con.execute("SELECT count(*) FROM odds_snapshots WHERE sport=?", [prefix]).fetchone()[0]
    con.close()
    print(f"Spent ~{spent} credits; wrote {wrote} rows ({total} total {prefix} snapshots).")


if __name__ == "__main__":
    main()
