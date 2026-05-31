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
from datetime import date
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression

from ..logging_conf import get_logger
from . import features as feat
from .features import TeamSnapshot, neutral_snapshot

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
    player_strength: float = 0.0


@dataclass
class ModelBundle:
    """A trained model + per-team state + metadata, loaded for runtime prediction.

    ``model`` is a fitted sklearn Pipeline (scaler + logistic). ``snapshots`` maps
    team -> :class:`TeamSnapshot` (elo/last_game_date/form), reconstructed from
    ``team_state.json``. ``meta`` carries the feature contract (hfa, feature_order,
    schema_version) so :meth:`load` can refuse a stale-shaped artifact rather than
    feed the model a wrong-width vector.
    """

    model: object
    snapshots: dict
    meta: dict

    @classmethod
    def load(cls, model_dir: str | Path) -> Optional["ModelBundle"]:
        model_dir = Path(model_dir)
        model_path = model_dir / "win_prob_model.pkl"
        state_path = model_dir / "team_state.json"
        meta_path = model_dir / "model_meta.json"
        if not (model_path.exists() and state_path.exists() and meta_path.exists()):
            log.info("No (complete) model in %s — Engine will abstain.", model_dir)
            return None
        try:
            meta = json.loads(meta_path.read_text())
            if (meta.get("schema_version") != feat.SCHEMA_VERSION
                    or meta.get("n_features") != feat.N_FEATURES
                    or meta.get("feature_order") != feat.FEATURE_ORDER):
                log.warning("Stale model artifact (schema v%s != v%s): run `make retrain`.",
                            meta.get("schema_version"), feat.SCHEMA_VERSION)
                return None
            model = pickle.loads(model_path.read_bytes())
            snapshots = {team: _snapshot_from_json(d)
                         for team, d in json.loads(state_path.read_text()).items()}
            log.info("Loaded model + %d team snapshots (schema v%s).",
                     len(snapshots), feat.SCHEMA_VERSION)
            return cls(model=model, snapshots=snapshots, meta=meta)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to load model bundle: %s", exc)
            return None

    def predict_home_prob(
        self,
        home_team: str,
        away_team: str,
        current_date: Optional[date] = None,
        home_stat: Optional[TeamStat] = None,  # noqa: ARG002 - kept for call compatibility
        away_stat: Optional[TeamStat] = None,  # noqa: ARG002
        home_availability: Optional[float] = None,
        away_availability: Optional[float] = None,
    ) -> float:
        """Probability the home team wins, from the full feature vector.

        The net-rating and roster features are **point-in-time**: they come from
        the team snapshot's season-to-date values, reset to 0 when the game falls
        in a later season than the snapshot (no prior games yet). Availability is
        passed live by the caller (tonight's injury-adjusted value), not taken
        from the stale snapshot; ``None`` -> 0 leaves it inert. ``home_stat`` /
        ``away_stat`` are ignored (kept only so existing callers don't break).
        """
        home_snap = self.snapshots.get(home_team) or neutral_snapshot()
        away_snap = self.snapshots.get(away_team) or neutral_snapshot()
        cur_season = feat.season_of(current_date) if current_date else None

        def eff(snap):
            stale = (cur_season is not None and snap.season is not None
                     and snap.season != cur_season)
            return (0.0, 0.0) if stale else (snap.net_eff, snap.roster)

        h_net, h_roster = eff(home_snap)
        a_net, a_roster = eff(away_snap)
        row = feat.build_feature_row(
            home_snap, away_snap, current_date, float(self.meta["hfa"]),
            TeamStat(h_net, 0.0), TeamStat(a_net, 0.0), h_roster, a_roster,
            home_availability=home_availability, away_availability=away_availability,
        )
        p = float(self.model.predict_proba([row])[0][1])
        # Post-hoc calibration (T defaults to 1.0 / no-op for older artifacts).
        return feat.temperature_scale(p, float(self.meta.get("temperature", 1.0)))

    def predict_participant_prob(self, home_team, away_team, participant, *,
                                 current_date: Optional[date] = None, **stats) -> float:
        """Win probability for whichever side ``participant`` names."""
        p_home = self.predict_home_prob(home_team, away_team, current_date=current_date, **stats)
        return p_home if participant == home_team else 1 - p_home


def _snapshot_from_json(d: dict) -> TeamSnapshot:
    lgd = d.get("last_game_date")
    return TeamSnapshot(
        elo=float(d.get("elo", feat.NEUTRAL_ELO)),
        last_game_date=date.fromisoformat(lgd) if lgd else None,
        form=float(d.get("form", feat.NEUTRAL_FORM)),
        games_played=int(d.get("games_played", 0)),
        net_eff=float(d.get("net_eff", 0.0)),
        roster=float(d.get("roster", 0.0)),
        season=d.get("season"),
        availability=float(d.get("availability", 0.0)),
    )
