"""Statistical win-probability models and the loadable prediction bundle.

* :class:`LogisticWinProbability` / :class:`RatingOptimizer` / :class:`MonteCarloPricer`
  are the modeling primitives (used by the training/optimization pipelines).
* :class:`ModelBundle` is what the Analytics Engine actually loads at runtime: a
  trained sklearn model + the current Elo ratings, with the net-rating
  enrichment baked in. It is the *only* sanctioned source of ``P_true`` — the
  Engine never trades on a producer-supplied probability.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression

from ..logging_conf import get_logger

log = get_logger("models")

DEFAULT_HFA = 50.0          # home-field advantage in Elo points
NET_RATING_TO_ELO = 20.0    # Elo points per 1.0 of net-rating differential


class LogisticWinProbability:
    """Logistic regression over engineered features (e.g. rating differential)."""

    def __init__(self):
        self.model = LogisticRegression()
        self.is_trained = False

    def train(self, X, y) -> None:
        self.model.fit(X, y)
        self.is_trained = True
        log.info("Logistic model trained on %d samples.", len(X))

    def predict_prob(self, features) -> float:
        if not self.is_trained:
            return 0.5
        return float(self.model.predict_proba([features])[0][1])


class RatingOptimizer:
    """Tune Elo K-factor and home-field advantage by minimizing log-loss."""

    def __init__(self, historical_results):
        # historical_results: list of (rating_a, rating_b, outcome)
        self.results = historical_results

    def _log_loss(self, params) -> float:
        k_factor, hfa = params
        total = 0.0
        for r_a, r_b, outcome in self.results:
            p = 1 / (1 + 10 ** ((r_b - (r_a + hfa)) / 400))
            p = max(min(p, 0.999), 0.001)
            total += -(outcome * np.log(p) + (1 - outcome) * np.log(1 - p))
        return total / len(self.results)

    def optimize(self):
        res = minimize(self._log_loss, [20.0, 50.0], method="L-BFGS-B",
                       bounds=[(10, 100), (0, 200)])
        log.info("Optimized: K=%.2f, HFA=%.2f", res.x[0], res.x[1])
        return res.x


class MonteCarloPricer:
    """Simulate game scores to price spreads/totals where closed forms are hard."""

    def __init__(self, iterations: int = 10000):
        self.iterations = iterations

    def simulate_game(self, mean_a, mean_b, std_dev=10, spread=3.5, total_line=210.5,
                      rng: Optional[np.random.Generator] = None) -> dict:
        rng = rng or np.random.default_rng()
        scores_a = rng.normal(mean_a, std_dev, self.iterations)
        scores_b = rng.normal(mean_b, std_dev, self.iterations)
        return {
            "ml_prob": float(np.mean(scores_a > scores_b)),
            "spread_prob": float(np.mean((scores_a - spread) > scores_b)),
            "over_prob": float(np.mean((scores_a + scores_b) > total_line)),
        }


@dataclass
class TeamStat:
    net_rating: float
    pace: float


@dataclass
class ModelBundle:
    """A trained model + ratings, loaded together for runtime prediction."""

    model: object
    ratings: dict

    @classmethod
    def load(cls, model_dir: str | Path) -> Optional["ModelBundle"]:
        model_dir = Path(model_dir)
        model_path = model_dir / "win_prob_model.pkl"
        ratings_path = model_dir / "current_ratings.json"
        if not (model_path.exists() and ratings_path.exists()):
            log.info("No trained model found in %s — Engine will abstain.", model_dir)
            return None
        try:
            model = pickle.loads(model_path.read_bytes())
            ratings = json.loads(ratings_path.read_text())
            log.info("Loaded model + %d team ratings.", len(ratings))
            return cls(model=model, ratings=ratings)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to load model bundle: %s", exc)
            return None

    def predict_home_prob(
        self,
        home_team: str,
        away_team: str,
        home_stat: Optional[TeamStat] = None,
        away_stat: Optional[TeamStat] = None,
    ) -> float:
        """Probability the home team wins, with optional net-rating enrichment."""
        r_home = self.ratings.get(home_team, 1500)
        r_away = self.ratings.get(away_team, 1500)
        adj_diff = (r_home + DEFAULT_HFA) - r_away
        if home_stat and away_stat:
            adj_diff += (home_stat.net_rating - away_stat.net_rating) * NET_RATING_TO_ELO
        return float(self.model.predict_proba([[adj_diff]])[0][1])

    def predict_participant_prob(self, home_team, away_team, participant, **stats) -> float:
        """Win probability for whichever side ``participant`` names."""
        p_home = self.predict_home_prob(home_team, away_team, **stats)
        return p_home if participant == home_team else 1 - p_home
