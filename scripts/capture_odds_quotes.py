"""Capture per-book h2h + totals from the LIVE Odds API into DuckDB odds_quotes.

The free, going-forward engine for the line-movement edge hunt. Run twice a day:

    --phase open   (morning)   keeps the FIRST sighting of each game's line
    --phase close  (near tip)  keeps the LATEST, and also updates the served
                               model's events.home/away_close (median h2h)

Open + close per game → the open→close movement dataset we mine for systematic
opener mispricings (positive CLV by construction). ~2 credits/call (h2h+totals),
so two runs/day ≈ 120/mo — inside the free 500/mo tier.

    python scripts/capture_odds_quotes.py --phase open
    python scripts/capture_odds_quotes.py --phase close
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.config import load_settings  # noqa: E402
from sportsball.db import Database  # noqa: E402
from sportsball.logging_conf import get_logger  # noqa: E402
from sportsball.store import Store  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_odds_markets_duckdb import rows_from_snapshot, DEFAULT_DB  # noqa: E402
from backfill_odds_history import parse_snapshot_et  # noqa: E402

log = get_logger("capture_odds_quotes")
LIVE_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"


def _key() -> str:
    return os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("ODDS_API_KEY=")), "")


def write_quotes(db_path: str, rows: list[tuple], phase: str, ts) -> int:
    con = duckdb.connect(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS odds_quotes (
            event_id TEXT, game_date DATE, market TEXT, bookmaker TEXT, side TEXT,
            point DOUBLE, price DOUBLE, phase TEXT, captured_at TIMESTAMP,
            PRIMARY KEY (event_id, market, bookmaker, side, phase));""")
    # 'open' keeps the first sighting (DO NOTHING); 'close' keeps the latest (DO UPDATE).
    conflict = ("DO NOTHING" if phase == "open"
                else "DO UPDATE SET point=excluded.point, price=excluded.price, captured_at=excluded.captured_at")
    con.executemany(
        f"INSERT INTO odds_quotes "
        f"(event_id, game_date, market, bookmaker, side, point, price, phase, captured_at) "
        f"VALUES (?,?,?,?,?,?,?,?,?) "
        f"ON CONFLICT (event_id, market, bookmaker, side, phase) {conflict};",
        [(eid, gd, mk, bk, side, pt, px, phase, ts) for eid, gd, mk, bk, side, pt, px in rows])
    n = con.execute("SELECT count(*) FROM odds_quotes WHERE phase=?", [phase]).fetchone()[0]
    con.close()
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="Capture per-book h2h+totals -> DuckDB odds_quotes")
    p.add_argument("--phase", choices=("open", "close"), required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--min-credits", type=int, default=10)
    args = p.parse_args()

    key = _key()
    if not key or "your_" in key:
        log.error("ODDS_API_KEY not set — skipping."); return
    try:
        r = requests.get(LIVE_URL, params={
            "apiKey": key, "regions": "us", "markets": "h2h,totals", "oddsFormat": "decimal"}, timeout=25)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.error("Odds API fetch failed: %s", exc); return
    remaining = int(r.headers.get("x-requests-remaining", -1))
    if 0 <= remaining < args.min_credits:
        log.warning("Quota low (%s) — skipping.", remaining); return

    snapshot = r.json()
    ts = datetime.now(timezone.utc)
    rows = rows_from_snapshot(snapshot)
    games = len({eid for eid, *_ in rows})
    total = write_quotes(args.db, rows, args.phase, ts)

    applied = 0
    if args.phase == "close":  # keep the served model's odds current (median h2h -> Postgres)
        store = Store(Database(load_settings().db))
        known = {row[0] for row in store.db.query(
            "SELECT event_id FROM events WHERE event_date >= now() - interval '2 days'")}
        for eid, hc, ac in parse_snapshot_et(snapshot):
            if eid in known:
                store.update_closing_odds(eid, hc, ac)
                applied += 1

    log.info("[%s] %d quotes for %d games -> odds_quotes (now %d %s rows); "
             "events updated %d; credits spent ~%s, remaining %s.",
             args.phase, len(rows), games, total, args.phase, applied,
             r.headers.get("x-requests-last", "2"), remaining)


if __name__ == "__main__":
    main()
