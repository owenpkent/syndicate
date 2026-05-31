"""Backfill the trained model's predictions as signals (no infra)."""
from sportsball.pipelines import backfill_signals

from fakes import FakeBundle, FakeDB


def test_one_signal_per_final_event_after_clearing():
    rows = [
        ("nba_20240115_lakers_at_celtics", "Celtics", "Lakers", None),
        ("nba_20240116_heat_at_bucks", "Bucks", "Heat", None),
    ]
    db = FakeDB(available=True, rows=rows)
    n = backfill_signals.run(db, bundle=FakeBundle(0.6))
    assert n == 2
    stmts = [s for s, _ in db.executed]
    assert any("DELETE FROM signals" in s for s in stmts)          # idempotent re-run
    assert sum("INSERT INTO signals" in s for s in stmts) == 2     # one per FINAL event


def test_no_model_records_nothing(monkeypatch):
    monkeypatch.setattr(backfill_signals.ModelBundle, "load", staticmethod(lambda d: None))
    db = FakeDB(available=True, rows=[("e", "H", "A", None)])
    assert backfill_signals.run(db) == 0
    assert db.executed == []
