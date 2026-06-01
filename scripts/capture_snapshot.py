"""Intraday per-book line snapshots -> DuckDB odds_snapshots (for book lead-lag).

Lead-lag — which book moves first, how long others lag — is the cleanest
deployable edge (beat the laggard to the number = CLV by certainty, no
prediction). It needs a TIME SERIES of per-book lines, which open/close can't
give. This appends EVERY snapshot (keyed by captured_at), so each book's
price-vs-time trajectory is reconstructable.

Run on a dense intraday cron for one in-season sport (MLB: most daily volume).
~2 credits/call (h2h+totals); every 2h over the game window ≈ 420/mo, free-tier.

    python scripts/capture_snapshot.py --sport-key baseball_mlb
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.logging_conf import get_logger  # noqa: E402
from sportsball.matching import canonical_event_id  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_odds_quotes import SPORTS, ET, _key, LIVE_URL  # noqa: E402
from backfill_odds_markets_duckdb import DEFAULT_DB  # noqa: E402

log = get_logger("capture_snapshot")


def parse_snapshot(snapshot: list[dict], prefix: str, ts):
    """Rows incl. commence_time + captured_at for every book/market/outcome."""
    out = []
    for ev in snapshot:
        home, away, when = ev.get("home_team"), ev.get("away_team"), ev.get("commence_time")
        if not (home and away and when):
            continue
        commence = datetime.fromisoformat(when.replace("Z", "+00:00"))
        et_date = commence.astimezone(ET).date().isoformat()
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
                    pt = oc.get("point")
                    out.append((eid, prefix, mkey, bk, oc.get("name"),
                                float(pt) if pt is not None else None, float(price),
                                commence, ts))
    return out


def main():
    p = argparse.ArgumentParser(description="Intraday per-book snapshot -> odds_snapshots")
    p.add_argument("--sport-key", default="baseball_mlb")
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
        log.error("[%s] fetch failed: %s", prefix, exc); return
    remaining = int(r.headers.get("x-requests-remaining", -1))
    if 0 <= remaining < args.min_credits:
        log.warning("Quota low (%s) — skipping.", remaining); return

    ts = datetime.now(timezone.utc)
    rows = parse_snapshot(r.json(), prefix, ts)
    con = duckdb.connect(args.db)
    con.execute(
        """CREATE TABLE IF NOT EXISTS odds_snapshots (
            event_id TEXT, sport TEXT, market TEXT, bookmaker TEXT, side TEXT,
            point DOUBLE, price DOUBLE, commence_time TIMESTAMP, captured_at TIMESTAMP,
            PRIMARY KEY (event_id, market, bookmaker, side, captured_at));""")
    con.executemany(
        "INSERT INTO odds_snapshots VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (event_id, market, bookmaker, side, captured_at) DO NOTHING;", rows)
    total = con.execute("SELECT count(*) FROM odds_snapshots WHERE sport=?", [prefix]).fetchone()[0]
    games = len({r[0] for r in rows})
    con.close()
    log.info("[%s] +%d quotes for %d games (%d total %s snapshots); credits ~%s, remaining %s.",
             prefix, len(rows), games, total, prefix, r.headers.get("x-requests-last", "2"), remaining)


if __name__ == "__main__":
    main()
