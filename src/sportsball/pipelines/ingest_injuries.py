"""Point-in-time roster availability — the injuries/availability lever.

The roadmap calls availability ("who is actually playing tonight") the single
highest-value missing signal, and the reason the season-roster feature was flat:
it ignored availability. This pipeline computes a **leakage-free, point-in-time**
availability scalar per team-game and writes it to ``team_availability_pit``,
where the trainer joins it as the ``availability_diff`` feature and the Engine's
serve path reads the latest value per team.

Two sources, one definition — *the season-to-date strength of the players
actually available tonight* (top rotation by prior minutes):

* **Historical (this module, offline from the DuckDB player logs):** the players
  who actually logged minutes in a game ARE the available roster for it. For each
  team-game we score those players using only their **prior** games this season
  (no outcome leakage), so a rested/absent star simply isn't in the set and the
  number drops. This is genuine point-in-time availability derivable from data we
  already ingest.
* **Live (serve, a documented hook):** tonight's availability = the same score
  over the full roster minus the players ruled out on the injury report. Same
  scalar, so train and serve stay symmetric.

With no availability rows the feature is inert (neutral 0) and the model behaves
exactly as before — same "blocked on data, plumbing ready" posture as the odds
feed. Run ``make ingest-injuries`` once the DuckDB player logs are loaded.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..store import Store

log = get_logger("ingest_injuries")

DUCKDB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "sportsball.duckdb"
PER_MIN_SCALE = 36.0  # mirror scripts/compute_player_strength.roster_strength


# -- pure core (unit-testable, no I/O) --------------------------------------
def available_strength(player_rows: list[dict], top_n: int = 8) -> float:
    """Strength scalar for a set of available players from their prior totals.

    Mirrors ``scripts/compute_player_strength.roster_strength``: take the ``top_n``
    by minutes, minutes-weighted mean of per-minute plus_minus, scaled to a small
    bounded number. Empty input → 0.0. Kept in lockstep so the availability score
    is directly comparable to the roster-strength feature.
    """
    rated = [(float(r.get("minutes") or 0.0), float(r.get("plus_minus") or 0.0))
             for r in player_rows]
    rated = [(m, pm) for m, pm in rated if m > 0]
    if not rated:
        return 0.0
    rated.sort(key=lambda t: t[0], reverse=True)
    top = rated[:top_n]
    total_min = sum(m for m, _ in top)
    if total_min <= 0:
        return 0.0
    return round(sum(pm for _, pm in top) / total_min * PER_MIN_SCALE / 100.0, 4)


def availability_rows(player_rows: list[dict], top_n: int = 8) -> list[tuple]:
    """(team_name, game_date, season, availability) per team-game, prior-only.

    ``player_rows``: dicts with ``team_name, season, game_date, player_name,
    minutes, plus_minus`` (one per player-game). For each team-season we walk game
    dates in order; a game's *available* roster is the players who appear in it,
    scored from their **cumulative prior** games — so the value reflects who was
    actually available without peeking at the current (or any future) game's
    production. A team's first game of a season scores 0 (no prior).
    """
    by_team_season: dict[tuple, list[dict]] = defaultdict(list)
    for r in player_rows:
        by_team_season[(r["team_name"], r["season"])].append(r)

    out: list[tuple] = []
    for (team, season), rows in by_team_season.items():
        by_date: dict[object, list[dict]] = defaultdict(list)
        for r in rows:
            by_date[r["game_date"]].append(r)
        cum: dict[str, list] = defaultdict(lambda: [0.0, 0.0])  # player -> [min, pm]
        for gd in sorted(by_date):
            present = {r["player_name"] for r in by_date[gd]}
            prior = [{"minutes": cum[p][0], "plus_minus": cum[p][1]} for p in present]
            out.append((team, gd, season, available_strength(prior, top_n)))
            for r in by_date[gd]:
                c = cum[r["player_name"]]
                c[0] += float(r.get("minutes") or 0.0)
                c[1] += float(r.get("plus_minus") or 0.0)
    return out


def _season_int(season) -> int:
    s = str(season)
    return int(s[:4]) if s[:4].isdigit() else 0


# -- I/O wrapper ------------------------------------------------------------
def load_player_rows(duckdb_path: Path):
    """Per-player-game rows from the DuckDB logs, or [] when unavailable."""
    if not duckdb_path.exists():
        log.warning("DuckDB %s not found — run ingest_player_stats_duckdb.py first.", duckdb_path)
        return []
    try:
        import duckdb  # lazy: host-only dependency
        con = duckdb.connect(str(duckdb_path), read_only=True)
    except Exception as exc:  # noqa: BLE001 - locked by a running ingest, etc.
        log.warning("Could not open DuckDB (%s); skipping.", exc)
        return []
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
    cols = ("team_name", "season", "game_date", "player_name", "minutes", "plus_minus")
    return [dict(zip(cols, r)) for r in raw]


def ingest(store: Store, duckdb_path: Path = DUCKDB_PATH) -> int:
    rows = load_player_rows(duckdb_path)
    if not rows:
        return 0
    out = availability_rows(rows)
    for team, game_date, season, availability in out:
        store.upsert_availability(team, game_date, _season_int(season), availability)
    log.info("Wrote %d point-in-time availability rows.", len(out))
    return len(out)


def main() -> None:
    argparse.ArgumentParser(description="Compute point-in-time roster availability").parse_args()
    store = Store(Database(load_settings().db))
    store.db.connect()
    if not store.available:
        log.error("Database unavailable.")
        return
    ingest(store)


if __name__ == "__main__":
    main()
