"""Precompute point-in-time (season-to-date) roster strength per team-game.

The current-season roster strength (``compute_player_strength.py``) added ~0 to
the model because it's a constant smeared across history. This computes the
**leakage-free** version: for each team-game, the team's roster strength using
only its players' games *strictly before* that date in that season. Written to
Postgres ``team_strength_pit`` and joined per-game by the trainer; the most
recent value per team is also pushed to ``team_advanced_stats.player_strength``.

    python scripts/precompute_roster_pit.py
"""
from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("roster_pit")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compute_player_strength as cps  # noqa: E402  (sibling: roster_strength)

DUCKDB_PATH = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"


def roster_pit_rows(player_rows: list[dict], top_n: int = 8) -> list[tuple]:
    """(team_name, game_date, season, roster_strength) per team-game, prior-only.

    ``player_rows``: dicts with ``team_name, season, game_date, player_name,
    minutes, plus_minus``. For each team-season, walk game dates in order and emit
    the roster strength from the cumulative *prior* games before adding that
    date's stats — so a team's first game of a season has strength 0 (no leakage).
    """
    by_ts: dict[tuple, list[dict]] = defaultdict(list)
    for r in player_rows:
        by_ts[(r["team_name"], r["season"])].append(r)

    out: list[tuple] = []
    for (team, season), rows in by_ts.items():
        by_date: dict[object, list[dict]] = defaultdict(list)
        for r in rows:
            by_date[r["game_date"]].append(r)
        cum: dict[str, list] = defaultdict(lambda: [0.0, 0.0])  # player -> [min, pm]
        for gd in sorted(by_date):
            prior = [{"minutes": m, "plus_minus": pm} for m, pm in cum.values()]
            out.append((team, gd, season, cps.roster_strength(prior, top_n)))
            for r in by_date[gd]:
                c = cum[r["player_name"]]
                c[0] += float(r.get("minutes") or 0.0)
                c[1] += float(r.get("plus_minus") or 0.0)
    return out


def _season_int(season) -> int:
    s = str(season)
    return int(s[:4]) if s[:4].isdigit() else 0


def main() -> None:
    if not DUCKDB_PATH.exists():
        log.warning("DuckDB %s not found — run ingest_player_stats_duckdb.py first.", DUCKDB_PATH)
        return
    try:
        import duckdb
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    except Exception as exc:  # noqa: BLE001 - locked by a running ingest, etc.
        log.warning("Could not open DuckDB (%s); skipping.", exc)
        return
    try:
        raw = con.execute(
            """
            SELECT team_name, season, game_date, player_name,
                   SUM(min) AS minutes, SUM(plus_minus) AS plus_minus
            FROM player_game_logs
            GROUP BY team_name, season, game_date, player_name
            """
        ).fetchall()
    finally:
        con.close()
    player_rows = [{"team_name": t, "season": s, "game_date": gd, "player_name": p,
                    "minutes": m, "plus_minus": pm} for t, s, gd, p, m, pm in raw]
    rows = roster_pit_rows(player_rows)
    log.info("Computed %d point-in-time roster values.", len(rows))
    if not rows:
        return

    import psycopg2
    from psycopg2.extras import execute_values
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "market_history"),
        user=os.getenv("POSTGRES_USER", "sportsball_admin"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme_in_env"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_strength_pit (
                    team_name TEXT, game_date TIMESTAMPTZ, season INTEGER,
                    roster_strength NUMERIC(10, 4), PRIMARY KEY (team_name, game_date))
            """)
            cur.execute("TRUNCATE team_strength_pit")
            execute_values(
                cur,
                "INSERT INTO team_strength_pit (team_name, game_date, season, roster_strength) "
                "VALUES %s ON CONFLICT (team_name, game_date) DO UPDATE "
                "SET roster_strength = EXCLUDED.roster_strength, season = EXCLUDED.season",
                [(t, gd, _season_int(s), strength) for t, gd, s, strength in rows],
                page_size=1000,
            )
            # Latest season-to-date value per team -> team_advanced_stats (serving's
            # current value; ensure the column exists on legacy tables first).
            cur.execute("ALTER TABLE team_advanced_stats "
                        "ADD COLUMN IF NOT EXISTS player_strength NUMERIC(10, 4)")
            latest: dict[str, tuple] = {}
            for t, gd, s, strength in rows:
                if t not in latest or gd > latest[t][0]:
                    latest[t] = (gd, strength)
            for t, (_gd, strength) in latest.items():
                cur.execute(
                    "INSERT INTO team_advanced_stats (team_name, player_strength) VALUES (%s, %s) "
                    "ON CONFLICT (team_name) DO UPDATE SET player_strength = EXCLUDED.player_strength",
                    (t, strength))
        conn.commit()
        log.info("Wrote %d team_strength_pit rows; refreshed player_strength for %d teams.",
                 len(rows), len(latest))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
