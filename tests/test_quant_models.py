"""Win-probability models and the loadable ModelBundle."""
import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression

from sportsball.quant.models import (
    LogisticWinProbability,
    ModelBundle,
    MonteCarloPricer,
    TeamStat,
)


class TestLogistic:
    def test_untrained_returns_half(self):
        assert LogisticWinProbability().predict_prob([100]) == 0.5

    def test_trained_separates_classes(self):
        m = LogisticWinProbability()
        m.train([[-500], [-400], [400], [500]], [0, 0, 1, 1])
        assert m.predict_prob([500]) > 0.5
        assert m.predict_prob([-500]) < 0.5


class TestModelBundle:
    def _trained_bundle(self, ratings):
        model = LogisticRegression().fit([[-500], [-400], [400], [500]], [0, 0, 1, 1])
        return ModelBundle(model=model, ratings=ratings)

    def test_load_missing_returns_none(self, tmp_path):
        assert ModelBundle.load(tmp_path) is None

    def test_home_prob_uses_ratings(self):
        bundle = self._trained_bundle({"Home": 1700, "Away": 1400})
        p = bundle.predict_home_prob("Home", "Away")
        assert 0.0 <= p <= 1.0
        assert p > 0.5  # higher-rated home team favored

    def test_participant_prob_flips_for_away(self):
        bundle = self._trained_bundle({"Home": 1700, "Away": 1400})
        p_home = bundle.predict_participant_prob("Home", "Away", "Home")
        p_away = bundle.predict_participant_prob("Home", "Away", "Away")
        assert p_home + p_away == pytest.approx(1.0)

    def test_net_rating_enrichment_shifts_probability(self):
        bundle = self._trained_bundle({"Home": 1500, "Away": 1500})
        base = bundle.predict_home_prob("Home", "Away")
        boosted = bundle.predict_home_prob(
            "Home", "Away",
            home_stat=TeamStat(net_rating=10, pace=100),
            away_stat=TeamStat(net_rating=-10, pace=100),
        )
        assert boosted > base


class TestMonteCarlo:
    def test_strong_team_favored_and_probs_bounded(self):
        rng = np.random.default_rng(0)
        out = MonteCarloPricer(iterations=5000).simulate_game(120, 100, rng=rng)
        assert out["ml_prob"] > 0.6
        for key in ("ml_prob", "spread_prob", "over_prob"):
            assert 0.0 <= out[key] <= 1.0
