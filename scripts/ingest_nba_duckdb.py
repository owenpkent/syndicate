"""Ingest real NBA game results from nba_api into a local DuckDB file.

A self-contained alternative to ``sportsball.pipelines.ingest_nba`` that lands
games in an embedded DuckDB database instead of the shared Postgres container —
no server, no schema-migration dance, just a portable ``.duckdb`` file.

Schema mirrors the Postgres ``events`` table (canonical ``event_id`` from
``sportsball.matching`` so rows align with the rest of the system) but with
NULL closing odds — scores are all the Elo + logistic model needs to train.

Writes are incremental and idempotent (upsert per season), so a run that gets
rate-limited by stats.nba.com can be re-run and will resume cleanly.

Usage:
    python scripts/ingest_nba_duckdb.py                 # 1983-84 .. current
    python scripts/ingest_nba_duckdb.py --start 1996-97 # narrower range
    python scripts/ingest_nba_duckdb.py --seasons 2024-25,2025-26
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

# Reuse the package's canonical id + game-pairing so DuckDB rows match Postgres.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.matching import canonical_event_id  # noqa: E402
from sportsball.pipelines.ingest_nba import build_games  # noqa: E402

NBA_SPORT_ID = 4
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"
# The first season stats.nba.com exposes complete, reliably parseable game logs.
EARLIEST_SEASON_YEAR = 1983
# 2025-26 is the current/just-completed season as of this writing.
CURRENT_SEASON_YEAR = 2025


def season_str(start_year: int) -> str:
    """1983 -> '1983-84'."""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def all_seasons(start_year: int, end_year: int) -> list[str]:
    return [season_str(y) for y in range(start_year, end_year + 1)]


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id    TEXT PRIMARY KEY,
            sport_id    INTEGER,
            season      TEXT,
            event_date  TIMESTAMP,
            home_team   TEXT NOT NULL,
            away_team   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'FINAL',
            home_score  INTEGER,
            away_score  INTEGER,
            home_close  DOUBLE,
            away_close  DOUBLE,
            ingested_at TIMESTAMP DEFAULT now()
        );
        """
    )


def fetch_season(season: str, retries: int = 3) -> list[dict]:
    """Pull one season's regular-season game logs, retrying transient errors."""
    from nba_api.stats.endpoints import leaguegamelog  # lazy host-only dep

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            df = leaguegamelog.LeagueGameLog(
                season=season,
                season_type_all_star="Regular Season",
                timeout=60,
            ).get_data_frames()[0]
            return df.to_dict("records")
        except Exception as e:  # noqa: BLE001 - network/timeouts are expected
            last_err = e
            wait = 2 * attempt
            print(f"  ! {season} attempt {attempt}/{retries} failed: {e} "
                  f"(retrying in {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"{season}: exhausted retries") from last_err


def upsert_games(con: duckdb.DuckDBPyConnection, season: str, games) -> int:
    rows = [
        (g.event_id, NBA_SPORT_ID, season, g.game_date, g.home_team,
         g.away_team, g.home_score, g.away_score)
        for g in games
    ]
    if not rows:
        return 0
    con.executemany(
        """
        INSERT INTO events
            (event_id, sport_id, season, event_date, home_team, away_team,
             home_score, away_score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'FINAL')
        ON CONFLICT (event_id) DO UPDATE SET
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            season     = excluded.season;
        """,
        rows,
    )
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest NBA results -> DuckDB")
    p.add_argument("--db", default=str(DEFAULT_DB), help="DuckDB file path")
    p.add_argument("--start", default=season_str(EARLIEST_SEASON_YEAR),
                   help="earliest season, e.g. 1996-97")
    p.add_argument("--end", default=season_str(CURRENT_SEASON_YEAR),
                   help="latest season, e.g. 2025-26")
    p.add_argument("--seasons", default=None,
                   help="explicit comma-separated list (overrides --start/--end)")
    p.add_argument("--sleep", type=float, default=1.0,
                   help="seconds between seasons (be polite to stats.nba.com)")
    args = p.parse_args()

    if args.seasons:
        seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    else:
        seasons = all_seasons(int(args.start[:4]), int(args.end[:4]))

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db)
    init_db(con)

    print(f"Ingesting {len(seasons)} seasons -> {args.db}", flush=True)
    total = 0
    failed: list[str] = []
    for season in seasons:
        print(f"[{season}] fetching...", flush=True)
        try:
            rows = fetch_season(season)
        except RuntimeError as e:
            print(f"  x {e} — skipping", flush=True)
            failed.append(season)
            continue
        games = build_games(rows)
        n = upsert_games(con, season, games)
        total += n
        print(f"  + {n} games", flush=True)
        time.sleep(args.sleep)

    grand = con.execute("SELECT count(*) FROM events").fetchone()[0]
    span = con.execute(
        "SELECT min(event_date)::TEXT, max(event_date)::TEXT FROM events"
    ).fetchone()
    con.close()

    print("-" * 50, flush=True)
    print(f"Ingested {total} games this run; {grand} total in DB.", flush=True)
    print(f"Date span: {span[0]} .. {span[1]}", flush=True)
    if failed:
        print(f"Failed seasons (re-run to retry): {', '.join(failed)}", flush=True)


if __name__ == "__main__":
    main()
