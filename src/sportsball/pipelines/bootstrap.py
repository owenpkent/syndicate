"""Bootstrap the Postgres schema + load history — non-destructively.

The running Postgres may predate the v0.2 normalized schema. ``config/init.sql``
is fully idempotent (``CREATE TABLE IF NOT EXISTS``), so applying it through the
app's own DB layer just adds the missing ``events``/``signals``/``trades`` tables
and leaves any legacy tables untouched — no volume wipe, no ``sudo``.

Then it loads the free NBA history from the local DuckDB store into ``events``
(FINAL, scores) so the modeling loop has data to train and evaluate on. The
DuckDB read is skipped cleanly if the file is absent or locked by a running
ingest (DuckDB is single-writer).
"""
from __future__ import annotations

from pathlib import Path

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..store import Store

log = get_logger("bootstrap")

NBA_SPORT_ID = 4
_REPO_ROOT = Path(__file__).resolve().parents[3]
INIT_SQL_CANDIDATES = [_REPO_ROOT / "config" / "init.sql", Path("/app/config/init.sql")]
DUCKDB_CANDIDATES = [_REPO_ROOT / "data" / "sportsball.duckdb", Path("data/sportsball.duckdb")]

MIGRATIONS = [
    # team_advanced_stats predates player_strength on existing volumes.
    "ALTER TABLE team_advanced_stats ADD COLUMN IF NOT EXISTS player_strength NUMERIC(10, 4)",
]


def _first_existing(paths) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def apply_schema(db: Database) -> bool:
    init_sql = _first_existing(INIT_SQL_CANDIDATES)
    if init_sql is None:
        log.error("init.sql not found in %s", [str(p) for p in INIT_SQL_CANDIDATES])
        return False
    log.info("Applying schema from %s ...", init_sql)
    # params=None so psycopg2 does NO %-interpolation — init.sql contains a
    # literal '%' in a comment that would otherwise be read as a placeholder.
    db.execute(init_sql.read_text(), None)  # multi-statement script in one call
    for stmt in MIGRATIONS:
        db.execute(stmt, None)
    log.info("Schema ensured (events / signals / trades / team_advanced_stats).")
    return True


def load_history(store: Store) -> int:
    """Copy FINAL games from the DuckDB events table into Postgres. Returns count."""
    duck = _first_existing(DUCKDB_CANDIDATES)
    if duck is None:
        log.warning("No DuckDB store found; skipping history load (run ingest first).")
        return 0
    try:
        import duckdb
        con = duckdb.connect(str(duck), read_only=True)
    except Exception as exc:  # noqa: BLE001 - locked by a running ingest, etc.
        log.warning("Could not open DuckDB (%s); skipping history load.", exc)
        return 0
    try:
        rows = con.execute(
            "SELECT event_id, sport_id, event_date, home_team, away_team, home_score, away_score "
            "FROM events WHERE home_score IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    for eid, sport_id, edate, home, away, hs, as_ in rows:
        store.upsert_event_result(eid, sport_id or NBA_SPORT_ID, edate, home, away,
                                  int(hs), int(as_), None, None)
    log.info("Loaded %d FINAL games from %s into events.", len(rows), duck)
    return len(rows)


def run(db: Database) -> bool:
    if not db.available:
        log.error("Postgres unavailable — is it up on the configured host/port?")
        return False
    if not apply_schema(db):
        return False
    load_history(Store(db))
    return True


def main() -> None:
    log.info("Bootstrap: ensuring schema + loading history...")
    run(Database(load_settings().db))


if __name__ == "__main__":
    main()
