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

from datetime import datetime as _dt
from zoneinfo import ZoneInfo
from sportsball.matching import canonical_event_id  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_odds_markets_duckdb import DEFAULT_DB  # noqa: E402
from backfill_odds_history import parse_snapshot_et  # noqa: E402

log = get_logger("capture_odds_quotes")
ET = ZoneInfo("America/New_York")
LIVE_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

# Odds API sport key -> our canonical event_id prefix. Steam is sport-agnostic, so
# any in-season sport works; matching to our (NBA) events table only applies to nba.
SPORTS = {
    "basketball_nba": "nba", "baseball_mlb": "mlb", "basketball_wnba": "wnba",
    "soccer_fifa_world_cup": "wc", "icehockey_nhl": "nhl",
}


def rows_from_live(snapshot: list[dict], prefix: str) -> list[tuple]:
    """Per-book (event_id, et_date, market, bookmaker, side, point, price) for any sport."""
    out = []
    for ev in snapshot:
        home, away, when = ev.get("home_team"), ev.get("away_team"), ev.get("commence_time")
        if not (home and away and when):
            continue
        et_date = _dt.fromisoformat(when.replace("Z", "+00:00")).astimezone(ET).date().isoformat()
        eid = canonical_event_id(prefix, et_date, away, home)
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


def _key() -> str:
    return os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(".env") if l.startswith("ODDS_API_KEY=")), "")


def write_quotes(db_path: str, rows: list[tuple], phase: str, sport: str, ts) -> int:
    con = duckdb.connect(db_path)
    con.execute(
        """CREATE TABLE IF NOT EXISTS odds_quotes (
            event_id TEXT, game_date DATE, market TEXT, bookmaker TEXT, side TEXT,
            point DOUBLE, price DOUBLE, phase TEXT, captured_at TIMESTAMP, sport TEXT,
            PRIMARY KEY (event_id, market, bookmaker, side, phase));""")
    if 'sport' not in [r[1] for r in con.execute("pragma table_info(odds_quotes)").fetchall()]:
        con.execute("ALTER TABLE odds_quotes ADD COLUMN sport TEXT")
    # 'open' keeps the first sighting (DO NOTHING); 'close' keeps the latest (DO UPDATE).
    conflict = ("DO NOTHING" if phase == "open"
                else "DO UPDATE SET point=excluded.point, price=excluded.price, captured_at=excluded.captured_at")
    con.executemany(
        f"INSERT INTO odds_quotes "
        f"(event_id, game_date, market, bookmaker, side, point, price, phase, captured_at, sport) "
        f"VALUES (?,?,?,?,?,?,?,?,?,?) "
        f"ON CONFLICT (event_id, market, bookmaker, side, phase) {conflict};",
        [(eid, gd, mk, bk, side, pt, px, phase, ts, sport) for eid, gd, mk, bk, side, pt, px in rows])
    n = con.execute("SELECT count(*) FROM odds_quotes WHERE sport=? AND phase=?", [sport, phase]).fetchone()[0]
    con.close()
    return n


def main() -> None:
    p = argparse.ArgumentParser(description="Capture per-book h2h+totals -> DuckDB odds_quotes")
    p.add_argument("--phase", choices=("open", "close"), required=True)
    p.add_argument("--sport-key", default="basketball_nba",
                   help="Odds API sport key, e.g. baseball_mlb, basketball_wnba, soccer_fifa_world_cup")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--min-credits", type=int, default=10)
    args = p.parse_args()

    prefix = SPORTS.get(args.sport_key, args.sport_key.split("_")[-1])

    key = _key()
    if not key or "your_" in key:
        log.error("ODDS_API_KEY not set — skipping."); return
    try:
        r = requests.get(LIVE_URL.format(sport_key=args.sport_key), params={
            "apiKey": key, "regions": "us", "markets": "h2h,totals", "oddsFormat": "decimal"}, timeout=25)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.error("[%s] Odds API fetch failed: %s", prefix, exc); return
    remaining = int(r.headers.get("x-requests-remaining", -1))
    if 0 <= remaining < args.min_credits:
        log.warning("Quota low (%s) — skipping.", remaining); return

    snapshot = r.json()
    ts = datetime.now(timezone.utc)
    rows = rows_from_live(snapshot, prefix)
    games = len({eid for eid, *_ in rows})
    total = write_quotes(args.db, rows, args.phase, prefix, ts)

    applied = 0
    if args.phase == "close" and prefix == "nba":  # only nba matches our served-model events
        store = Store(Database(load_settings().db))
        known = {row[0] for row in store.db.query(
            "SELECT event_id FROM events WHERE event_date >= now() - interval '2 days'")}
        for eid, hc, ac in parse_snapshot_et(snapshot):
            if eid in known:
                store.update_closing_odds(eid, hc, ac)
                applied += 1

    log.info("[%s %s] %d quotes for %d games -> odds_quotes (now %d %s rows); "
             "events updated %d; credits spent ~%s, remaining %s.",
             prefix, args.phase, len(rows), games, total, args.phase, applied,
             r.headers.get("x-requests-last", "2"), remaining)


if __name__ == "__main__":
    main()
