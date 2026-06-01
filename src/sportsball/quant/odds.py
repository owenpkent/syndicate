"""Odds conversion, expected value, and Kelly sizing.

Pure functions, no third-party imports — the foundation everything else prices
against.
"""
from __future__ import annotations


def american_to_decimal(american_odds: float) -> float:
    """Convert American odds (-110, +150) to decimal (1.91, 2.50).

    Returns 0.0 for the sentinel/off-board values (0 and the Rundown ``0.0001``).
    """
    if not american_odds or american_odds == 0.0001:
        return 0.0
    if american_odds > 0:
        return round((american_odds / 100) + 1, 4)
    return round((100 / abs(american_odds)) + 1, 4)


def implied_prob(decimal_odds: float) -> float:
    """Market-implied probability of a decimal price (``1 / odds``)."""
    return 1.0 / decimal_odds if decimal_odds > 0 else 0.0


def devig_two_way(home_odds: float, away_odds: float):
    """No-vig implied probability the HOME side wins from two decimal prices.

    Removes the bookmaker margin by normalizing the two implied probabilities to
    sum to 1 (proportional / "multiplicative" de-vig). Returns ``None`` when either
    price is missing or ≤ 1 (no real market), so callers degrade to neutral.
    """
    if not home_odds or not away_odds or home_odds <= 1 or away_odds <= 1:
        return None
    ih, ia = 1.0 / home_odds, 1.0 / away_odds
    total = ih + ia
    return ih / total if total > 0 else None


def calculate_ev(true_prob: float, odds: float) -> float:
    """Expected value per unit staked: ``EV = P_true * odds - 1``."""
    return (true_prob * odds) - 1


def calculate_kelly_fraction(ev: float, odds: float, multiplier: float = 0.25) -> float:
    """Fractional Kelly stake.

    ``f* = EV / (odds - 1)``; the returned stake is ``multiplier * max(0, f*)``
    so a negative edge never produces a position. Odds at or below 1.0 (no
    payout) stake nothing.
    """
    if odds <= 1:
        return 0.0
    f_star = ev / (odds - 1)
    return multiplier * max(0.0, f_star)
