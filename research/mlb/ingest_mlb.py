"""Ingest real MLB game results -> data/mlb.duckdb (free, no key).

The unlock for a baseball win-probability model: the project's model pipeline
(`walk_forward` Elo + the shared feature builder) is sport-agnostic, but `events`
holds only NBA. This pulls regular-season finals from the official **MLB Stats
API** (statsapi.mlb.com) into a parallel store so the same model can be trained
and measured on baseball.

One row per game keyed by `game_pk`; `officialDate` is the ET game date.

    python research/mlb/ingest_mlb.py                       # 2010..current
    python research/mlb/ingest_mlb.py --start 2015 --end 2024
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

log = get_logger("ingest_mlb")
SCHED = "https://statsapi.mlb.com/api/v1/schedule"
DEFAULT_DB = str(REPO / "data" / "mlb.duckdb")


def season_games(season: int) -> list[tuple]:
    """Final regular-season games: (game_pk, date, season, home, away, hs, as,
    home_sp_id, home_sp, away_sp_id, away_sp). Starters via the cheap
    probablePitcher hydrate (~99% coverage on completed games)."""
    r = requests.get(SCHED, params={"sportId": 1, "season": season, "gameType": "R",
                                    "hydrate": "probablePitcher"}, timeout=30)
    r.raise_for_status()
    out = []
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("detailedState") != "Final":
                continue
            t = g["teams"]
            hs, as_ = t["home"].get("score"), t["away"].get("score")
            if hs is None or as_ is None:
                continue
            hp, ap = t["home"].get("probablePitcher") or {}, t["away"].get("probablePitcher") or {}
            out.append((g["gamePk"], g.get("officialDate") or g["gameDate"][:10], season,
                        t["home"]["team"]["name"], t["away"]["team"]["name"], int(hs), int(as_),
                        hp.get("id"), hp.get("fullName"), ap.get("id"), ap.get("fullName")))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest MLB results -> data/mlb.duckdb")
    p.add_argument("--start", type=int, default=2010)
    p.add_argument("--end", type=int, default=2026)
    p.add_argument("--db", default=DEFAULT_DB)
    args = p.parse_args()

    con = duckdb.connect(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS games (
        game_pk BIGINT PRIMARY KEY, game_date DATE, season INTEGER,
        home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER,
        home_sp_id BIGINT, home_sp TEXT, away_sp_id BIGINT, away_sp TEXT);""")
    # Backfill starter columns onto a pre-existing (sp-less) table.
    cols = {c[1] for c in con.execute("PRAGMA table_info(games)").fetchall()}
    for col, typ in (("home_sp_id", "BIGINT"), ("home_sp", "TEXT"),
                     ("away_sp_id", "BIGINT"), ("away_sp", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE games ADD COLUMN {col} {typ}")
    grand = 0
    for season in range(args.start, args.end + 1):
        try:
            rows = season_games(season)
        except Exception as exc:  # noqa: BLE001
            log.warning("season %d fetch failed: %s", season, exc); continue
        if rows:
            # Upsert so a re-run backfills starters (and refreshes the live season).
            con.executemany(
                """INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT (game_pk) DO UPDATE SET
                     home_score=excluded.home_score, away_score=excluded.away_score,
                     home_sp_id=excluded.home_sp_id, home_sp=excluded.home_sp,
                     away_sp_id=excluded.away_sp_id, away_sp=excluded.away_sp;""", rows)
        grand += len(rows)
        log.info("season %d: %d final games", season, len(rows))
        time.sleep(0.3)
    total = con.execute("SELECT count(*) FROM games").fetchone()[0]
    span = con.execute("SELECT min(game_date), max(game_date) FROM games").fetchone()
    con.close()
    log.info("done. fetched %d; %d total games in store (%s -> %s).",
             grand, total, span[0], span[1])


if __name__ == "__main__":
    main()
