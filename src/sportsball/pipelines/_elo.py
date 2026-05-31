"""Shared Elo simulation used by both the optimizer and the trainer.

Both pipelines walk the same historical results forward, updating ratings game
by game. Factoring it here removes the near-duplicate ``simulate_elo`` /
``generate_features`` code the original had in two files.
"""
from __future__ import annotations

import numpy as np

from ..db import Database

HISTORY_QUERY = """
    SELECT event_date, home_team, away_team, home_score, away_score
    FROM events
    WHERE status = 'FINAL' AND home_score IS NOT NULL
    ORDER BY event_date ASC
"""


def fetch_history(db: Database) -> list[tuple]:
    return db.query(HISTORY_QUERY)


def _expected_home(r_home: float, r_away: float, hfa: float) -> float:
    return 1 / (1 + 10 ** ((r_away - (r_home + hfa)) / 400))


def walk_forward(results, k_factor: float, hfa: float):
    """Yield ``(rating_diff, expected_home, actual)`` per game and final ratings.

    ``rating_diff`` is the HFA-adjusted differential (the logistic feature);
    ``actual`` is 1 for a home win, 0 for away, 0.5 for a draw.
    """
    ratings: dict[str, float] = {}
    rows = []
    for _date, home, away, hs, as_ in results:
        r_home = ratings.get(home, 1500)
        r_away = ratings.get(away, 1500)
        exp_home = _expected_home(r_home, r_away, hfa)
        if hs > as_:
            actual = 1.0
        elif hs < as_:
            actual = 0.0
        else:
            actual = 0.5
        rows.append(((r_home + hfa) - r_away, exp_home, actual))
        shift = k_factor * (actual - exp_home)
        ratings[home] = r_home + shift
        ratings[away] = r_away - shift
    return rows, ratings


def mean_log_loss(results, k_factor: float, hfa: float) -> float:
    rows, _ = walk_forward(results, k_factor, hfa)
    if not rows:
        return 1.0
    total = 0.0
    for _diff, p, actual in rows:
        p = max(min(p, 0.999), 0.001)
        total += -(actual * np.log(p) + (1 - actual) * np.log(1 - p))
    return total / len(rows)
