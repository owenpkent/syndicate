"""Backfill final scores and closing lines from The Rundown into Postgres.

Replaces both ``historical_scraper.py`` (single range) and
``backfill_manager.py`` (multi-sport orchestration). Use ``--managed`` for the
preset multi-sport slate, or ``--start/--end/--sport`` for one range.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

import requests

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..quant.odds import american_to_decimal

log = get_logger("backfill")

# Preset slates for --managed mode: (sport_id, start, end). NBA=4 NFL=2 MLB=1 NHL=6
MANAGED_SLATES = [
    (4, "2023-10-24", "2024-04-14"),
    (2, "2023-09-07", "2024-01-07"),
    (1, "2024-03-20", "2024-09-29"),
    (6, "2023-10-10", "2024-04-18"),
]

UPSERT = """
    INSERT INTO historical_results
    (event_id, sport_id, event_date, home_team, away_team, home_score, away_score, home_odds, away_odds)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (event_id) DO UPDATE SET
        home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score,
        home_odds = EXCLUDED.home_odds, away_odds = EXCLUDED.away_odds
"""


def _side_odds(home_team, away_team, participants) -> tuple[float, float]:
    home_odds = away_odds = 0.0
    for part in participants:
        lines = part.get("lines", [])
        if not lines:
            continue
        prices = lines[0].get("prices", {})
        aff = "19" if "19" in prices else next(iter(prices), None)
        if not aff:
            continue
        dec = american_to_decimal(prices[aff].get("price"))
        name = (part.get("name") or "").lower()
        if home_team.lower() in name or name in home_team.lower():
            home_odds = dec
        elif away_team.lower() in name or name in away_team.lower():
            away_odds = dec
    return home_odds, away_odds


def scrape_date(date_str: str, sport_id: int, api_key: str, retries: int = 3) -> list[tuple]:
    url = f"https://therundown.io/api/v2/sports/{sport_id}/events/{date_str}"
    delay = 10
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"X-TheRundown-Key": api_key}, timeout=15)
            if resp.status_code == 429:
                log.warning("Rate limited on %s; retry in %ss (%d/%d)", date_str, delay, attempt + 1, retries)
                time.sleep(delay); delay *= 2; continue
            resp.raise_for_status()
            records = []
            for event in resp.json().get("events", []):
                score = event.get("score", {})
                if score.get("event_status") != "STATUS_FINAL":
                    continue
                teams = event.get("teams", [])
                home = next((t for t in teams if not t.get("is_away")), {}).get("name")
                away = next((t for t in teams if t.get("is_away")), {}).get("name")
                ml = next((m for m in event.get("markets", []) if m.get("market_id") == 1), None)
                home_odds, away_odds = _side_odds(home, away, ml.get("participants", [])) if ml else (0.0, 0.0)
                records.append((event.get("event_id"), sport_id, event.get("event_date"),
                                home, away, score.get("score_home"), score.get("score_away"),
                                home_odds, away_odds))
            return records
        except Exception as exc:  # noqa: BLE001
            log.error("Error scraping %s (%d): %s", date_str, attempt + 1, exc)
            time.sleep(delay); delay *= 2
    return []


def scrape_range(db: Database, sport_id: int, start: str, end: str, api_key: str) -> int:
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    total = 0
    while cur <= end_dt:
        date_str = cur.strftime("%Y-%m-%d")
        records = scrape_date(date_str, sport_id, api_key)
        if records:
            db.executemany(UPSERT, records)
            log.info("%s: upserted %d records", date_str, len(records))
            total += len(records)
        time.sleep(10)  # rate-limit courtesy
        cur += timedelta(days=1)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Sportsball historical backfill")
    parser.add_argument("--managed", action="store_true", help="Run the preset multi-sport slate")
    parser.add_argument("--start"); parser.add_argument("--end")
    parser.add_argument("--sport", type=int, default=4)
    args = parser.parse_args()

    settings = load_settings()
    if not settings.has_live_rundown_key():
        log.error("RUNDOWN_API_KEY not set; cannot backfill.")
        return
    db = Database(settings.db)

    if args.managed:
        for sport_id, start, end in MANAGED_SLATES:
            log.info("--- Backfilling sport %d: %s to %s ---", sport_id, start, end)
            scrape_range(db, sport_id, start, end, settings.rundown_api_key)
            time.sleep(120)
    elif args.start and args.end:
        scrape_range(db, args.sport, args.start, args.end, settings.rundown_api_key)
    else:
        parser.error("provide --managed or both --start and --end")


if __name__ == "__main__":
    main()
