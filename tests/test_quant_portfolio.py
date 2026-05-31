"""Portfolio risk manager: exposure ceiling + correlation penalty."""
import pytest

from sportsball.config import StrategyConfig
from sportsball.quant.portfolio import PortfolioRiskManager

STRATEGY = StrategyConfig(max_global_exposure_pct=0.15, correlation_penalty_multiplier=0.5)


def mgr():
    return PortfolioRiskManager(STRATEGY)


def test_unconstrained_passes_through():
    assert mgr().evaluate_risk("SRC-E1-T", 0.05, []) == pytest.approx(0.05)


def test_downsized_to_available_headroom():
    active = [{"market_id": "X-E9-T", "size": 0.13}]
    assert mgr().evaluate_risk("SRC-E1-T", 0.05, active) == pytest.approx(0.02)


def test_rejected_when_full():
    active = [{"market_id": "X-E9-T", "size": 0.15}]
    assert mgr().evaluate_risk("SRC-E1-T", 0.05, active) == 0


def test_correlation_penalty_when_same_event_open():
    active = [{"market_id": "SRC-E1-OTHER", "size": 0.01}]
    # 0.01 + 0.02 < 0.15 (no downsizing), but same event -> 0.5x penalty.
    assert mgr().evaluate_risk("SRC-E1-T", 0.02, active) == pytest.approx(0.01)
