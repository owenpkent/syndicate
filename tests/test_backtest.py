"""Pure betting-simulation logic for the walk-forward backtest (no I/O)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backtest as bt  # noqa: E402


def test_efficient_market_yields_no_bets():
    # When the book prices at our own probability, no side clears the EV buffer.
    p = [0.7, 0.4, 0.55, 0.62]
    m = bt.simulate(p, list(p), [1, 0, 1, 1], vig=0.0, kelly=0.25, buffer=0.02)
    assert m["bets"] == 0
    assert m["bankroll"] == bt.START_BANKROLL


def test_efficient_market_with_vig_still_no_bets():
    p = [0.7, 0.4, 0.55]
    m = bt.simulate(p, list(p), [1, 0, 1], vig=0.045, kelly=0.25, buffer=0.02)
    assert m["bets"] == 0  # vig only makes the EV worse


def test_mispriced_book_is_profitable():
    # We think 70%; the book thinks 50% (offers ~2.0). Outcomes land 70% -> +ROI.
    n = 100
    p_us = [0.7] * n
    p_mkt = [0.5] * n
    outcomes = [1] * 70 + [0] * 30
    m = bt.simulate(p_us, p_mkt, outcomes, vig=0.0, kelly=0.25, buffer=0.02)
    assert m["bets"] == n
    assert m["roi"] > 0
    assert m["bankroll"] > bt.START_BANKROLL
