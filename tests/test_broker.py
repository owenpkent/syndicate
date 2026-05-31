"""Broker queue + exposure semantics, exercised against an in-memory FakeRedis."""
import pytest

from sportsball.broker import ACTIVE_TRADES, Broker, MARKET_SIGNALS

from fakes import FakeRedis


def _broker():
    b = Broker.__new__(Broker)  # bypass real redis connection
    b.r = FakeRedis()
    return b


def test_push_pop_roundtrip():
    b = _broker()
    b.push(MARKET_SIGNALS, {"market_id": "X", "odds": 2.0})
    assert b.queue_depth(MARKET_SIGNALS) == 1
    assert b.pop(MARKET_SIGNALS, block=False) == {"market_id": "X", "odds": 2.0}
    assert b.pop(MARKET_SIGNALS, block=False) is None


def test_exposure_set_total_clear():
    b = _broker()
    b.set_exposure("A", 0.05)
    b.set_exposure("B", 0.10)
    assert b.total_exposure() == pytest.approx(0.15)
    assert {t["market_id"] for t in b.active_trades()} == {"A", "B"}
    b.clear_exposure("A")
    assert b.total_exposure() == pytest.approx(0.10)
    assert ACTIVE_TRADES in b.r.hashes


def test_reliable_queue_moves_to_inflight_then_acks():
    b = _broker()
    b.push("q", {"n": 1})
    raw = b.r.brpoplpush("q", "q:inflight")
    assert b.r.llen("q") == 0
    assert b.r.llen("q:inflight") == 1
    b.ack("q:inflight", raw)
    assert b.r.llen("q:inflight") == 0
