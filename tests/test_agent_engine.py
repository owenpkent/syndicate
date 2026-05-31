"""Analytics Engine signal processing — the abstain/EV/risk/arb decision tree."""
import pytest

from sportsball.agents.engine import process_signal
from sportsball.broker import EXECUTION_SIGNALS
from sportsball.config import StrategyConfig
from sportsball.quant.arbitrage import ArbitrageEngine

from fakes import FakeBroker, FakeBundle, FakeDB

STRAT = StrategyConfig(safety_buffer_ev=0.02, kelly_multiplier=0.25, require_model=True)


def _signal(market_id="MOCK-E1-Celtics", odds=2.0, participant="Celtics"):
    return {
        "market_id": market_id,
        "odds": odds,
        "metadata": {"source": "MOCK", "matchup": "Lakers @ Celtics", "participant": participant},
    }


def _run(data, bundle, broker=None, db=None, arb=None, strat=STRAT):
    broker = broker or FakeBroker()
    process_signal(data, bundle=bundle, db=db or FakeDB(available=False),
                   broker=broker, arb=arb or ArbitrageEngine(), strategy=strat)
    return broker


class TestAbstain:
    def test_no_model_no_trade(self):
        broker = _run(_signal(), bundle=None)
        assert broker.pushed[EXECUTION_SIGNALS] == []

    def test_no_model_logs_nothing_to_db(self):
        db = FakeDB(available=True)
        _run(_signal(), bundle=None, db=db)
        assert db.executed == []  # nothing logged because no modeled prob


class TestValueSignal:
    def test_positive_edge_emits_execution(self):
        broker = _run(_signal(odds=2.0), bundle=FakeBundle(0.6))  # EV = 0.2
        pushed = broker.pushed[EXECUTION_SIGNALS]
        assert len(pushed) == 1
        assert pushed[0]["market_id"] == "MOCK-E1-Celtics"
        assert pushed[0]["fraction"] == pytest.approx(0.05)  # 0.25 * 0.2/1.0

    def test_below_buffer_rejected_but_logged(self):
        db = FakeDB(available=True)
        broker = _run(_signal(odds=2.0), bundle=FakeBundle(0.5), db=db)  # EV = 0
        assert broker.pushed[EXECUTION_SIGNALS] == []
        assert len(db.executed) == 1  # market_history insert still happens

    def test_producer_prob_used_only_when_not_require_model(self):
        data = _signal(odds=2.0)
        data["true_prob"] = 0.6
        relaxed = StrategyConfig(require_model=False, safety_buffer_ev=0.02)
        broker = _run(data, bundle=None, strat=relaxed)
        assert len(broker.pushed[EXECUTION_SIGNALS]) == 1


class TestArbitrage:
    def test_two_sided_overlay_emits_arbitrage(self):
        arb = ArbitrageEngine()
        broker = FakeBroker()
        # Both sides priced at 2.10 -> Σ(1/odds) < 1.
        _run(_signal("MOCK-E1-Celtics", 2.10, "Celtics"), bundle=None, broker=broker, arb=arb)
        _run(_signal("MOCK-E1-Lakers", 2.10, "Lakers"), bundle=None, broker=broker, arb=arb)
        arb_msgs = [m for m in broker.pushed[EXECUTION_SIGNALS] if m.get("type") == "ARBITRAGE"]
        assert len(arb_msgs) == 1
        assert arb_msgs[0]["event_id"] == "E1"
