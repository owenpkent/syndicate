"""Pure quantitative primitives — no DB, broker, or network imports.

Kept dependency-light on purpose so the unit suite (and a backtest) can import
the math directly. The heavier model classes live in :mod:`sportsball.quant.models`.
"""
from .odds import american_to_decimal, calculate_ev, calculate_kelly_fraction, implied_prob
from .poisson import poisson_probability, joint_poisson_matrix
from .arbitrage import ArbitrageEngine
from .portfolio import PortfolioRiskManager

__all__ = [
    "american_to_decimal",
    "calculate_ev",
    "calculate_kelly_fraction",
    "implied_prob",
    "poisson_probability",
    "joint_poisson_matrix",
    "ArbitrageEngine",
    "PortfolioRiskManager",
]
