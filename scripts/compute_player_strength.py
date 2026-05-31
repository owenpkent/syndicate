"""Compute a per-team roster-strength scalar from the DuckDB player logs.

Reads season-to-date player box scores from ``data/sportsball.duckdb``
(``player_game_logs``, written by ``ingest_player_stats_duckdb.py``), derives one
strength number per team, and UPSERTs it into Postgres
``team_advanced_stats.player_strength`` — where the modeling pipeline picks it up
as the ``player_strength_diff`` feature.

**What this is (and isn't):** a *season roster strength* — a minutes-weighted blend
of the top rotation players' per-minute plus_minus. It is deliberately coarse:
season-level, not lineup-of-the-night, and it ignores injuries / availability. It
is a useful prior, not a depth chart. When the DuckDB file or table is absent the
script no-ops cleanly (the column stays NULL → the feature contributes 0).

Usage:
    python scripts/compute_player_strength.py                 # current season
    python scripts/compute_player_strength.py --season 2024-25
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("player_strength")

DUCKDB_PATH = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"
DEFAULT_SEASON = "2025-26"
PER_MIN_SCALE = 36.0  # express per-minute plus_minus as a per-36 figure, then /100 below


# -- pure core (unit-testable, no I/O) --------------------------------------
def roster_strength(player_rows: list[dict], top_n: int = 8) -> float:
    """One strength scalar for a team-season from its players' minutes + plus_minus.

    ``player_rows``: dicts with ``minutes`` and ``plus_minus`` (season totals).
    Takes the ``top_n`` players by minutes, computes each one's per-minute
    plus_minus, and returns their minutes-weighted mean scaled to a small bounded
    number (roughly a per-36 net rating / 100). Empty input → 0.0.
    """
    rated = [
        (float(r.get("minutes") or 0.0), float(r.get("plus_minus") or 0.0))
        for r in player_rows
    ]
    rated = [(m, pm) for m, pm in rated if m > 0]
    if not rated:
        return 0.0
    rated.sort(key=lambda t: t[0], reverse=True)
    top = rated[:top_n]
    total_min = sum(m for m, _ in top)
    if total_min <= 0:
        return 0.0
    # minutes-weighted mean of per-minute plus_minus == sum(pm)/sum(min)
    per_min = sum(pm for _, pm in top) / total_min
    return round(per_min * PER_MIN_SCALE / 100.0, 4)


def aggregate_by_team(rows: list[dict]) -> dict[str, float]:
    """Group player-season rows by ``team_name`` and compute each team's strength.

    ``rows``: dicts with ``team_name``, ``minutes``, ``plus_minus``.
    """
    by_team: dict[str, list[dict]] = {}
    for r in rows:
        by_team.setdefault(r["team_name"], []).append(r)
    return {team: roster_strength(players) for team, players in by_team.items()}


# -- I/O wrapper ------------------------------------------------------------
def _load_player_season(con, season: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT team_name, player_name,
               SUM(min)        AS minutes,
               SUM(plus_minus) AS plus_minus
        FROM player_game_logs
        WHERE season = ?
        GROUP BY team_name, player_name
        """,
        [season],
    ).fetchall()
    return [{"team_name": t, "player_name": p, "minutes": m, "plus_minus": pm}
            for t, p, m, pm in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute team roster strength -> Postgres")
    ap.add_argument("--season", default=DEFAULT_SEASON)
    ap.add_argument("--duckdb", default=str(DUCKDB_PATH))
    args = ap.parse_args()

    if not Path(args.duckdb).exists():
        log.warning("DuckDB %s not found — nothing to compute. "
                    "Run ingest_player_stats_duckdb.py first.", args.duckdb)
        return

    import duckdb
    con = duckdb.connect(args.duckdb, read_only=True)
    tables = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
    if "player_game_logs" not in tables:
        log.warning("No player_game_logs table in %s — nothing to compute.", args.duckdb)
        return
    players = _load_player_season(con, args.season)
    con.close()
    strengths = aggregate_by_team(players)
    if not strengths:
        log.warning("No player rows for season %s.", args.season)
        return
    log.info("Computed roster strength for %d teams (season %s).", len(strengths), args.season)

    import psycopg2
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "market_history"),
        user=os.getenv("POSTGRES_USER", "sportsball_admin"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme_in_env"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE team_advanced_stats "
                        "ADD COLUMN IF NOT EXISTS player_strength NUMERIC(10, 4)")
            for team, strength in strengths.items():
                cur.execute(
                    """
                    INSERT INTO team_advanced_stats (team_name, player_strength)
                    VALUES (%s, %s)
                    ON CONFLICT (team_name) DO UPDATE SET player_strength = EXCLUDED.player_strength
                    """,
                    (team, strength),
                )
        conn.commit()
        log.info("Upserted player_strength for %d teams.", len(strengths))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
