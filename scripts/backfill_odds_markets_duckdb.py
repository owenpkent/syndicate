"""Backfill PER-BOOK h2h + totals odds from The Odds API history -> DuckDB.

The one-time "use it or lose it" pull before the paid plan is cancelled: the
historical endpoint (~10 credits/market/call) is the only source of per-book
prices and totals/spreads lines. We keep EVERY book's quote (not just the median
we stored in events.home_close) so we can measure line-shopping value, and we add
totals (O/U) — the market that pairs with team_advanced_game_logs (pace) — into a
new research table `odds_quotes`.

One snapshot per real game-day, near the ET evening tip (near-closing). ET-date
fix matches nba_api game dates. Credit-safe (--budget), idempotent (upsert on
event_id+market+bookmaker+side+point).

    python scripts/backfill_odds_markets_duckdb.py --limit 1     # test (~20 credits)
    python scripts/backfill_odds_markets_duckdb.py               # full (~13k credits)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.config import load_settings  # noqa: E402
from sportsball.db import Database  # noqa: E402
from sportsball.matching import canonical_event_id  # noqa: E402
from sportsball.store import Store  # noqa: E402

ET = ZoneInfo("America/New_York")
HIST_URL = "https://api.the-odds-api.com/v4/historical/sports/basketball_nba/odds"
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"
MARKETS = "h2h,totals"  # 2 markets x 10 credits = ~20/game-day


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS odds_quotes (
            event_id    TEXT,
            game_date   DATE,
            market      TEXT,      -- 'h2h' | 'totals'
            bookmaker   TEXT,
            side        TEXT,      -- team name | 'Over' | 'Under'
            point       DOUBLE,    -- totals line / spread point; NULL for h2h
            price       DOUBLE,    -- decimal odds
            PRIMARY KEY (event_id, market, bookmaker, side)
        );
        """
    )


def rows_from_snapshot(snapshot: list[dict]) -> list[tuple]:
    out = []
    for ev in snapshot:
        home, away, when = ev.get("home_team"), ev.get("away_team"), ev.get("commence_time")
        if not (home and away and when):
            continue
        dt = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(ET)
        et_date = dt.date().isoformat()
        eid = canonical_event_id("nba", et_date, away, home)
        for book in ev.get("bookmakers", []):
            bk = book.get("key")
            for market in book.get("markets", []):
                mkey = market.get("key")
                if mkey not in ("h2h", "totals"):
                    continue
                for oc in market.get("outcomes", []):
                    price = oc.get("price")
                    if price is None:
                        continue
                    point = oc.get("point")
                    out.append((eid, et_date, mkey, bk, oc.get("name"),
                                float(point) if point is not None else None, float(price)))
    return out


def fetch_snapshot(api_key: str, ts_iso: str) -> tuple[list[dict], int, int]:
    r = requests.get(HIST_URL, params={
        "apiKey": api_key, "regions": "us", "markets": MARKETS,
        "oddsFormat": "decimal", "date": ts_iso}, timeout=30)
    r.raise_for_status()
    return (r.json().get("data", []),
            int(r.headers.get("x-requests-last", 20)),
            int(r.headers.get("x-requests-remaining", -1)))


def game_days(store: Store, since: str) -> list[str]:
    rows = store.db.query(
        "SELECT DISTINCT event_date FROM events "
        "WHERE event_date >= %s AND home_score IS NOT NULL ORDER BY event_date ASC", (since,))
    return [r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10] for r in rows]


def upsert(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    con.executemany(
        "INSERT INTO odds_quotes (event_id, game_date, market, bookmaker, side, point, price) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT (event_id, market, bookmaker, side) "
        "DO UPDATE SET point = excluded.point, price = excluded.price;", rows)
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill per-book h2h+totals -> DuckDB odds_quotes")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--since", default="2022-07-01")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--budget", type=int, default=13000, help="max credits to spend")
    p.add_argument("--sleep", type=float, default=0.3)
    args = p.parse_args()

    import os
    key = os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("ODDS_API_KEY=")), "")
    if not key or "your_" in key:
        print("ODDS_API_KEY not set"); return

    store = Store(Database(load_settings().db))
    days = game_days(store, args.since)
    if args.limit:
        days = days[:args.limit]
    con = duckdb.connect(args.db)
    init_db(con)
    print(f"{len(days)} game-days x ~20 credits = ~{len(days)*20} credits (budget {args.budget}).")

    spent = total = 0
    i = 0
    for i, d in enumerate(days, 1):
        ts = (datetime.fromisoformat(d).replace(tzinfo=timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%dT00:00:00Z")
        try:
            snap, last, remaining = fetch_snapshot(key, ts)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {d}: {exc}"); continue
        spent += last
        n = upsert(con, rows_from_snapshot(snap))
        total += n
        if i % 25 == 0 or i == len(days):
            print(f"  [{i}/{len(days)}] {d}: +{n} quotes | spent {spent}, remaining {remaining}",
                  flush=True)
        if spent >= args.budget:
            print(f"  budget {args.budget} reached at {d}."); break
        time.sleep(args.sleep)

    grand = con.execute("SELECT count(*) FROM odds_quotes").fetchone()[0]
    books = con.execute("SELECT count(DISTINCT bookmaker) FROM odds_quotes").fetchone()[0]
    con.close()
    print("-" * 50)
    print(f"Spent ~{spent} credits over {i} days; {total} quotes this run, "
          f"{grand} total across {books} books.")


if __name__ == "__main__":
    main()
