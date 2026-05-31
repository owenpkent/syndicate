"""Sniper paper execution + exposure tracking on the store contract."""
import random

from sportsball.agents.sniper import handle_value, simulate_fill
from sportsball.store import Store

from fakes import FakeBroker, FakeDB


def _exec_signal():
    return {"market_id": "MOCK-E1-Celtics", "event_id": "E1", "side": "HOME",
            "home_team": "Celtics", "away_team": "Lakers", "source": "MOCK",
            "odds": 2.0, "fraction": 0.05}


class TestSimulateFill:
    def test_success_within_tolerance(self):
        result = simulate_fill(2.0, tolerance=0.005, rng=random.Random(1))
        assert result["status"] == "SUCCESS"
        assert result["executed_odds"] < 2.0

    def test_failure_when_slippage_exceeds_tolerance(self):
        assert simulate_fill(2.0, tolerance=0.0001, rng=random.Random(1))["status"] == "FAILED"


class TestHandleValue:
    def test_success_records_open_trade_and_exposure(self):
        broker, store = FakeBroker(), Store(FakeDB(available=True))
        handle_value(_exec_signal(), mode="PAPER", tolerance=0.005,
                     store=store, broker=broker, rng=random.Random(1))
        assert broker._exposure["MOCK-E1-Celtics"] == 0.05
        sql = " ".join(s for s, _ in store.db.executed)
        assert "INSERT INTO trades" in sql

    def test_non_paper_mode_takes_no_exposure(self):
        broker, store = FakeBroker(), Store(FakeDB(available=True))
        handle_value(_exec_signal(), mode="LIVE", tolerance=0.005,
                     store=store, broker=broker, rng=random.Random(1))
        assert broker._exposure == {}
        assert store.db.executed == []
