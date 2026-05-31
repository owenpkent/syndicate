"""Bootstrap pipeline: idempotent schema apply (no real Postgres/DuckDB)."""
from sportsball.pipelines import bootstrap

from fakes import FakeDB


def test_apply_schema_runs_ddl_and_migration():
    db = FakeDB(available=True)
    assert bootstrap.apply_schema(db) is True
    sql = " ".join(s for s, _ in db.executed)
    assert "CREATE TABLE IF NOT EXISTS events" in sql
    assert "CREATE TABLE IF NOT EXISTS signals" in sql
    assert "ADD COLUMN IF NOT EXISTS player_strength" in sql


def test_run_bails_when_db_unavailable():
    assert bootstrap.run(FakeDB(available=False)) is False
