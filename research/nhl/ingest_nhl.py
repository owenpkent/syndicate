"""Ingest NHL regular-season results -> data/nhl.duckdb (free, no key).

NHL games always have a winner (OT/shootout — no ties since 2005-06), so hockey
is a binary, goal-margin sport like MLB/NBA and reuses the same `walk_forward`
Elo pipeline. Source: the public NHL web API (api-web.nhle.com). Teams are
enumerated per season from the standings (handles relocations/abbrev churn);
games are pulled per club-season and de-duped by game id.

    python research/nhl/ingest_nhl.py                 # 2010-11 .. 2025-26
    python research/nhl/ingest_nhl.py --start 2015 --end 2024
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb
import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))
from sportsball.logging_conf import get_logger  # noqa: E402

log = get_logger("ingest_nhl")
UA = {"User-Agent": "sportsball-nhl/0.1"}
DEFAULT_DB = str(REPO / "data" / "nhl.duckdb")


def season_teams(start_year: int) -> list[str]:
    """Team abbrevs active in a season, from mid-season standings."""
    r = requests.get(f"https://api-web.nhle.com/v1/standings/{start_year + 1}-01-15",
                     headers=UA, timeout=20)
    r.raise_for_status()
    return [t["teamAbbrev"]["default"] for t in r.json().get("standings", [])]


def club_games(team: str, season: str) -> list[dict]:
    r = requests.get(f"https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}",
                     headers=UA, timeout=20)
    if r.status_code != 200:
        return []
    return r.json().get("games", [])


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest NHL results -> data/nhl.duckdb")
    p.add_argument("--start", type=int, default=2010, help="first season start year")
    p.add_argument("--end", type=int, default=2025, help="last season start year")
    p.add_argument("--db", default=DEFAULT_DB)
    args = p.parse_args()

    con = duckdb.connect(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS games (
        game_id BIGINT PRIMARY KEY, game_date DATE, season INTEGER,
        home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER);""")
    grand = 0
    for start_year in range(args.start, args.end + 1):
        season = f"{start_year}{start_year + 1}"
        try:
            teams = season_teams(start_year)
        except Exception as exc:  # noqa: BLE001
            log.warning("season %s standings failed: %s", season, exc); continue
        seen, rows = set(), []
        for team in teams:
            for g in club_games(team, season):
                if g.get("gameType") != 2 or g.get("gameState") not in ("OFF", "FINAL"):
                    continue
                gid = g.get("id") or g.get("gameId")
                if gid is None or gid in seen:
                    continue
                h, a = g.get("homeTeam", {}), g.get("awayTeam", {})
                if h.get("score") is None or a.get("score") is None:
                    continue
                seen.add(gid)
                rows.append((gid, g.get("gameDate"), start_year,
                             h.get("abbrev"), a.get("abbrev"),
                             int(h["score"]), int(a["score"])))
            time.sleep(0.15)
        if rows:
            con.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?) "
                            "ON CONFLICT (game_id) DO UPDATE SET "
                            "home_score=excluded.home_score, away_score=excluded.away_score;", rows)
        grand += len(rows)
        log.info("season %s: %d regular-season games (%d teams)", season, len(rows), len(teams))
    total = con.execute("SELECT count(*) FROM games").fetchone()[0]
    span = con.execute("SELECT min(game_date), max(game_date) FROM games").fetchone()
    con.close()
    log.info("done. fetched %d; %d total in store (%s -> %s).", grand, total, span[0], span[1])


if __name__ == "__main__":
    main()
