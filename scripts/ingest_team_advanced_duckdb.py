"""Ingest NBA team ADVANCED game logs from nba_api into DuckDB.

The possession-based analog of ``ingest_nba_duckdb.py`` / ``ingest_player_stats_
duckdb.py``. ``TeamGameLogs`` with ``measure_type='Advanced'`` returns every
team's per-game offensive/defensive/net rating, pace, and shooting efficiency for
a whole season in one call — the raw material for a possession-based strength
feature (upgrades the margin-based ``net_rating_diff``) and for totals/pace models.

Each team-game is tagged with the SAME canonical ``event_id`` as the team-level
``events`` table (derived from the home/away full team names within each game),
so ``team_advanced_game_logs`` joins straight onto ``events`` and the player logs.

Advanced tracking starts in 1996-97; earlier seasons return nothing. Writes are
incremental and idempotent (upsert on team_id + game_id), so a rate-limited run
re-runs and resumes cleanly.

Usage:
    python scripts/ingest_team_advanced_duckdb.py                  # 1996-97 .. current
    python scripts/ingest_team_advanced_duckdb.py --seasons 2024-25,2025-26
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.matching import canonical_event_id  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"
EARLIEST_SEASON_YEAR = 1996  # advanced tracking begins 1996-97
CURRENT_SEASON_YEAR = 2025

# Advanced columns carried straight through from nba_api.
STAT_COLS = [
    "MIN", "OFF_RATING", "DEF_RATING", "NET_RATING", "PACE", "TS_PCT",
    "EFG_PCT", "AST_PCT", "AST_TO", "OREB_PCT", "DREB_PCT", "REB_PCT",
    "TM_TOV_PCT", "PIE",
]


def season_str(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def all_seasons(start_year: int, end_year: int) -> list[str]:
    return [season_str(y) for y in range(start_year, end_year + 1)]


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    cols = ",\n            ".join(f"{c.lower()} DOUBLE" for c in STAT_COLS)
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS team_advanced_game_logs (
            team_id      BIGINT,
            game_id      TEXT,
            event_id     TEXT,
            season       TEXT,
            game_date    TIMESTAMP,
            team_name    TEXT,
            team_abbreviation TEXT,
            is_home      BOOLEAN,
            wl           TEXT,
            {cols},
            ingested_at  TIMESTAMP DEFAULT now(),
            PRIMARY KEY (team_id, game_id)
        );
        """
    )


def event_ids_for_game(rows: list[dict]) -> str | None:
    """Canonical event_id shared by both team rows of one game (home has "vs.")."""
    home = next((r for r in rows if "vs." in (r.get("MATCHUP") or "")), None)
    away = next((r for r in rows if "@" in (r.get("MATCHUP") or "")), None)
    if not home or not away:
        return None
    return canonical_event_id("nba", home["GAME_DATE"], away["TEAM_NAME"], home["TEAM_NAME"])


def _num(v):
    """nba_api yields NaN for stats absent in older seasons; store NULL."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check


def build_rows(records: list[dict], season: str) -> list[tuple]:
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_game[r.get("GAME_ID")].append(r)

    out: list[tuple] = []
    for game_id, rows in by_game.items():
        event_id = event_ids_for_game(rows)
        for r in rows:
            is_home = "vs." in (r.get("MATCHUP") or "")
            base = (
                int(r["TEAM_ID"]), str(game_id), event_id, season,
                str(r["GAME_DATE"]), r.get("TEAM_NAME"),
                r.get("TEAM_ABBREVIATION"), is_home, r.get("WL"),
            )
            out.append(base + tuple(_num(r.get(c)) for c in STAT_COLS))
    return out


def fetch_season(season: str, retries: int = 3) -> list[dict]:
    from nba_api.stats.endpoints import teamgamelogs  # lazy host-only dep

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            df = teamgamelogs.TeamGameLogs(
                season_nullable=season,
                season_type_nullable="Regular Season",
                measure_type_player_game_logs_nullable="Advanced",
                timeout=60,
            ).get_data_frames()[0]
            return df.to_dict("records")
        except Exception as e:  # noqa: BLE001 - network/timeouts expected
            last_err = e
            wait = 2 * attempt
            print(f"  ! {season} attempt {attempt}/{retries} failed: {e} "
                  f"(retrying in {wait}s)", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"{season}: exhausted retries") from last_err


def upsert(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    if not rows:
        return 0
    fixed = ["team_id", "game_id", "event_id", "season", "game_date",
             "team_name", "team_abbreviation", "is_home", "wl"]
    cols = fixed + [c.lower() for c in STAT_COLS]
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols
                        if c not in ("team_id", "game_id"))
    con.executemany(
        f"INSERT INTO team_advanced_game_logs ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (team_id, game_id) DO UPDATE SET {updates};",
        rows,
    )
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest NBA team advanced game logs -> DuckDB")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--start", default=season_str(EARLIEST_SEASON_YEAR))
    p.add_argument("--end", default=season_str(CURRENT_SEASON_YEAR))
    p.add_argument("--seasons", default=None,
                   help="explicit comma-separated list (overrides --start/--end)")
    p.add_argument("--sleep", type=float, default=1.0)
    args = p.parse_args()

    if args.seasons:
        seasons = [s.strip() for s in args.seasons.split(",") if s.strip()]
    else:
        seasons = all_seasons(int(args.start[:4]), int(args.end[:4]))

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db)
    init_db(con)

    print(f"Ingesting team advanced logs for {len(seasons)} seasons -> {args.db}", flush=True)
    total = 0
    failed: list[str] = []
    for season in seasons:
        print(f"[{season}] fetching...", flush=True)
        try:
            records = fetch_season(season)
        except RuntimeError as e:
            print(f"  x {e} — skipping", flush=True)
            failed.append(season)
            continue
        rows = build_rows(records, season)
        n = upsert(con, rows)
        total += n
        print(f"  + {n} team-games", flush=True)
        time.sleep(args.sleep)

    grand = con.execute("SELECT count(*) FROM team_advanced_game_logs").fetchone()[0]
    linked = con.execute(
        "SELECT count(*) FROM team_advanced_game_logs WHERE event_id IS NOT NULL").fetchone()[0]
    con.close()

    print("-" * 50, flush=True)
    print(f"Ingested {total} team-games this run; {grand} total.", flush=True)
    print(f"Linked to an event_id: {linked}/{grand}", flush=True)
    if failed:
        print(f"Failed seasons (re-run to resume): {', '.join(failed)}", flush=True)


if __name__ == "__main__":
    main()
