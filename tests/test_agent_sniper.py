"""Sniper paper execution + exposure tracking."""
import random

from sportsball.agents.sniper import handle_value, simulate_fill

from fakes import FakeBroker, FakeDB


class TestSimulateFill:
    def test_success_within_tolerance(self):
        rng = random.Random(1)
        result = simulate_fill(2.0, tolerance=0.005, rng=rng)
        assert result["status"] == "SUCCESS"
        assert result["executed_odds"] < 2.0  # slippage reduces odds

    def test_failure_when_slippage_exceeds_tolerance(self):
        rng = random.Random(1)
        result = simulate_fill(2.0, tolerance=0.0001, rng=rng)
        assert result["status"] == "FAILED"


class TestHandleValue:
    def test_successful_fill_records_exposure_and_trade(self):
        broker, db = FakeBroker(), FakeDB(available=True)
        handle_value({"market_id": "MOCK-E1-T", "odds": 2.0, "fraction": 0.05},
                     mode="PAPER", tolerance=0.005, db=db, broker=broker, rng=random.Random(1))
        assert broker._exposure["MOCK-E1-T"] == 0.05
        assert any("trade_history" in sql for sql, _ in db.executed)

    def test_non_paper_mode_skips_execution(self):
        broker, db = FakeBroker(), FakeDB(available=True)
        handle_value({"market_id": "MOCK-E1-T", "odds": 2.0, "fraction": 0.05},
                     mode="LIVE", tolerance=0.005, db=db, broker=broker, rng=random.Random(1))
        assert broker._exposure == {}  # no exposure taken
