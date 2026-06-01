"""Analytics Engine signal processing — abstain/EV/risk/arb on the store contract."""
import pytest

from sportsball.agents.engine import process_signal
from sportsball.broker import EXECUTION_SIGNALS
from sportsball.config import StrategyConfig
from sportsball.quant.arbitrage import ArbitrageEngine
from sportsball.store import Store

from fakes import FakeBroker, FakeBundle, FakeDB

STRAT = StrategyConfig(safety_buffer_ev=0.02, kelly_multiplier=0.25, require_model=True)


def _signal(market_id="MOCK-E1-Celtics", odds=2.0, participant="Celtics", source="MOCK"):
    return {
        "market_id": market_id,
        "odds": odds,
        "metadata": {"source": source, "matchup": "Lakers @ Celtics", "participant": participant},
    }


def _run(data, bundle, broker=None, db=None, arb=None, strat=STRAT):
    broker = broker or FakeBroker()
    store = Store(db or FakeDB(available=False))
    process_signal(data, bundle=bundle, store=store, broker=broker,
                   arb=arb or ArbitrageEngine(), strategy=strat)
    return broker, store


class TestAbstain:
    def test_no_model_no_trade(self):
        broker, _ = _run(_signal(), bundle=None)
        assert broker.pushed[EXECUTION_SIGNALS] == []

    def test_no_model_persists_nothing(self):
        _, store = _run(_signal(), bundle=None, db=FakeDB(available=True))
        assert store.db.executed == []  # abstains before any write


class TestValueSignal:
    def test_positive_edge_emits_enriched_execution(self):
        broker, _ = _run(_signal(odds=2.0), bundle=FakeBundle(0.6))  # EV = 0.2
        pushed = broker.pushed[EXECUTION_SIGNALS]
        assert len(pushed) == 1
        msg = pushed[0]
        assert msg["market_id"] == "MOCK-E1-Celtics"
        assert msg["event_id"] == "E1"
        assert msg["side"] == "HOME"          # Celtics is home in "Lakers @ Celtics"
        assert msg["fraction"] == pytest.approx(0.05)

    def test_records_event_and_signal(self):
        _, store = _run(_signal(odds=2.0), bundle=FakeBundle(0.6), db=FakeDB(available=True))
        sql = " ".join(s for s, _ in store.db.executed)
        assert "INSERT INTO events" in sql
        assert "INSERT INTO signals" in sql

    def test_below_buffer_logs_but_no_execution(self):
        broker, store = _run(_signal(odds=2.0), bundle=FakeBundle(0.5), db=FakeDB(available=True))
        assert broker.pushed[EXECUTION_SIGNALS] == []
        assert any("INSERT INTO signals" in s for s, _ in store.db.executed)


class TestMarketFeature:
    def test_devigged_market_prob_reaches_model(self):
        captured = {}

        class RecordingBundle:
            meta = {}

            def predict_participant_prob(self, home, away, participant, **stats):
                captured.update(stats)
                return 0.6

        arb, broker = ArbitrageEngine(), FakeBroker()
        # Both sides at 1.90 -> after de-vig P(home) == 0.5.
        _run(_signal("BOOKA-E1-Celtics", odds=1.90, participant="Celtics", source="BOOKA"),
             bundle=RecordingBundle(), broker=broker, arb=arb)
        _run(_signal("BOOKB-E1-Lakers", odds=1.90, participant="Lakers", source="BOOKB"),
             bundle=RecordingBundle(), broker=broker, arb=arb)
        assert captured.get("home_market_prob") == pytest.approx(0.5, abs=1e-6)

    def test_one_sided_book_leaves_market_neutral(self):
        captured = {}

        class RecordingBundle:
            meta = {}

            def predict_participant_prob(self, home, away, participant, **stats):
                captured.update(stats)
                return 0.6

        _run(_signal("BOOKA-E1-Celtics", odds=1.90, participant="Celtics", source="BOOKA"),
             bundle=RecordingBundle())
        assert captured.get("home_market_prob") is None  # only one side known


class TestUncertaintyAwareKelly:
    def test_less_certain_model_stakes_less(self):
        # Same edge/odds; a model whose calibrator tempers it harder should size
        # smaller than a confident (no-calibration) one.
        confident = FakeBundle(0.6, meta={})
        uncertain = FakeBundle(0.6, meta={"calibration": {"method": "temperature", "temperature": 3.0}})
        b1, _ = _run(_signal(odds=2.0), bundle=confident)
        b2, _ = _run(_signal(odds=2.0), bundle=uncertain)
        f_conf = b1.pushed[EXECUTION_SIGNALS][0]["fraction"]
        f_unc = b2.pushed[EXECUTION_SIGNALS][0]["fraction"]
        assert 0 < f_unc < f_conf

    def test_scaling_off_ignores_calibration(self):
        strat = StrategyConfig(safety_buffer_ev=0.02, kelly_multiplier=0.25,
                               require_model=True, uncertainty_scaling=False)
        uncertain = FakeBundle(0.6, meta={"calibration": {"method": "temperature", "temperature": 3.0}})
        b, _ = _run(_signal(odds=2.0), bundle=uncertain, strat=strat)
        assert b.pushed[EXECUTION_SIGNALS][0]["fraction"] == pytest.approx(0.05)


class TestLineShopping:
    def test_bets_best_available_number_across_venues(self):
        # Same game/side quoted by two books; the Engine should price and emit
        # against the better (higher) number, not whichever arrived last.
        arb, broker = ArbitrageEngine(), FakeBroker()
        _run(_signal("BOOKA-E1-Celtics", odds=1.91, participant="Celtics", source="BOOKA"),
             bundle=FakeBundle(0.6), broker=broker, arb=arb)
        _run(_signal("BOOKB-E1-Celtics", odds=2.05, participant="Celtics", source="BOOKB"),
             bundle=FakeBundle(0.6), broker=broker, arb=arb)
        values = [m for m in broker.pushed[EXECUTION_SIGNALS] if m.get("type") != "ARBITRAGE"]
        # The second evaluation sees the best standing number (2.05 from BOOKB).
        last = values[-1]
        assert last["odds"] == pytest.approx(2.05)
        assert last["source"] == "BOOKB"
        assert last["market_id"] == "BOOKB-E1-Celtics"
        # event_id stays canonical regardless of which venue's price we took.
        assert last["event_id"] == "E1"

    def test_upgrades_to_better_prior_quote(self):
        # A great price arrives first, a worse one second; the second evaluation
        # must shop back up to the better standing number.
        arb, broker = ArbitrageEngine(), FakeBroker()
        _run(_signal("BOOKB-E1-Celtics", odds=2.20, participant="Celtics", source="BOOKB"),
             bundle=FakeBundle(0.6), broker=broker, arb=arb)
        _run(_signal("BOOKA-E1-Celtics", odds=1.91, participant="Celtics", source="BOOKA"),
             bundle=FakeBundle(0.6), broker=broker, arb=arb)
        last = [m for m in broker.pushed[EXECUTION_SIGNALS] if m.get("type") != "ARBITRAGE"][-1]
        assert last["odds"] == pytest.approx(2.20)
        assert last["source"] == "BOOKB"


class TestArbitrage:
    def test_two_sided_overlay_emits_arbitrage(self):
        arb, broker = ArbitrageEngine(), FakeBroker()
        _run(_signal("MOCK-E1-Celtics", 2.10, "Celtics"), bundle=None, broker=broker, arb=arb)
        _run(_signal("MOCK-E1-Lakers", 2.10, "Lakers"), bundle=None, broker=broker, arb=arb)
        arbs = [m for m in broker.pushed[EXECUTION_SIGNALS] if m.get("type") == "ARBITRAGE"]
        assert len(arbs) == 1
        assert arbs[0]["event_id"] == "E1"
        assert {leg["side"] for leg in arbs[0]["legs"]} == {"HOME", "AWAY"}
