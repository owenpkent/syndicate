"""Closing Line Value tool — pure summarize/verdict + the analyze() two-views."""
import pytest

from sportsball.tools import clv as clvmod
from sportsball.store import HOME, AWAY, Store

from fakes import FakeDB


class TestClv:
    def test_beats_close_is_positive(self):
        assert clvmod.clv(2.10, 2.00) == pytest.approx(0.05)

    def test_worse_than_close_is_negative(self):
        assert clvmod.clv(1.90, 2.00) < 0

    def test_missing_price_is_none(self):
        assert clvmod.clv(0, 2.0) is None
        assert clvmod.clv(2.0, 0) is None
        assert clvmod.clv(None, 2.0) is None


class TestSummarize:
    def test_picks_side_close_and_aggregates(self):
        rows = [
            (2.10, HOME, 2.00, 1.80),   # CLV +5%
            (1.95, AWAY, 1.80, 2.00),   # CLV -2.5%
        ]
        s = clvmod.summarize(rows)
        assert s["n"] == 2
        assert s["avg_clv"] == pytest.approx((0.05 - 0.025) / 2)
        assert s["beat_rate"] == pytest.approx(0.5)

    def test_empty_is_none(self):
        assert clvmod.summarize([]) is None
        assert clvmod.summarize([(0, HOME, 0, 0)]) is None


class TestVerdict:
    def test_thresholds(self):
        assert "ALPHA-POSITIVE" in clvmod.verdict(0.03)
        assert "MARGINAL" in clvmod.verdict(0.005)
        assert "SUB-PAR" in clvmod.verdict(-0.01)


def test_analyze_returns_both_views(monkeypatch):
    store = Store(FakeDB(available=True))
    monkeypatch.setattr(store, "signal_clv_rows", lambda: [(2.10, HOME, 2.0, 1.8)])
    monkeypatch.setattr(store, "clv_rows", lambda: [(2.05, HOME, 2.0, 1.8)])
    out = clvmod.analyze(store)
    assert out["signal"]["avg_clv"] == pytest.approx(0.05)
    assert out["trade"]["avg_clv"] == pytest.approx(0.025)


def test_analyze_none_when_no_rows(monkeypatch):
    store = Store(FakeDB(available=True))
    monkeypatch.setattr(store, "signal_clv_rows", lambda: [])
    monkeypatch.setattr(store, "clv_rows", lambda: [])
    assert clvmod.analyze(store) is None
