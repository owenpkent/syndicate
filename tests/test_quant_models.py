"""Win-probability models and the loadable ModelBundle (multi-feature, v2)."""
import json
import pickle
from datetime import date

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from sportsball.quant import features as feat
from sportsball.quant.features import TeamSnapshot
from sportsball.quant.models import (
    LogisticWinProbability,
    ModelBundle,
    MonteCarloPricer,
    TeamStat,
)

HFA = 50.0


def _trained_pipeline():
    """A 9-feature logistic Pipeline where elo and net-rating both push the label."""
    rng = np.random.default_rng(0)
    X, y = [], []
    for _ in range(600):
        elo = rng.uniform(-400, 400)
        net = rng.uniform(-15, 15)
        form = rng.uniform(-0.5, 0.5)
        # cols: elo, net, rest, b2b_h, b2b_a, form, player_strength, availability, market
        row = [elo, net, rng.uniform(-3, 3), 0.0, 0.0, form,
               rng.uniform(-1, 1), rng.uniform(-0.5, 0.5), rng.uniform(-1, 1)]
        score = elo / 120 + net / 5 + form
        y.append(1 if rng.uniform() < 1 / (1 + np.exp(-score)) else 0)
        X.append(row)
    return Pipeline([("scaler", StandardScaler()),
                     ("lr", LogisticRegression(max_iter=1000))]).fit(X, y)


def _meta(**over):
    m = {"schema_version": feat.SCHEMA_VERSION, "feature_order": feat.FEATURE_ORDER,
         "n_features": feat.N_FEATURES, "hfa": HFA}
    m.update(over)
    return m


def _bundle(snapshots, meta=None):
    return ModelBundle(model=_trained_pipeline(), snapshots=snapshots, meta=meta or _meta())


class TestLogistic:
    def test_untrained_returns_half(self):
        assert LogisticWinProbability().predict_prob([100]) == 0.5

    def test_trained_separates_classes(self):
        m = LogisticWinProbability()
        m.train([[-500], [-400], [400], [500]], [0, 0, 1, 1])
        assert m.predict_prob([500]) > 0.5
        assert m.predict_prob([-500]) < 0.5


class TestModelBundleLoad:
    def test_load_missing_returns_none(self, tmp_path):
        assert ModelBundle.load(tmp_path) is None

    def test_stale_schema_version_abstains(self, tmp_path):
        (tmp_path / "win_prob_model.pkl").write_bytes(pickle.dumps(_trained_pipeline()))
        (tmp_path / "team_state.json").write_text(json.dumps({}))
        (tmp_path / "model_meta.json").write_text(json.dumps(_meta(schema_version=1)))
        assert ModelBundle.load(tmp_path) is None  # width/version guard

    def test_load_roundtrip(self, tmp_path):
        (tmp_path / "win_prob_model.pkl").write_bytes(pickle.dumps(_trained_pipeline()))
        (tmp_path / "team_state.json").write_text(json.dumps(
            {"Home": {"elo": 1600, "last_game_date": "2024-01-10", "form": 0.7, "games_played": 5}}))
        (tmp_path / "model_meta.json").write_text(json.dumps(_meta()))
        bundle = ModelBundle.load(tmp_path)
        assert bundle is not None
        assert bundle.snapshots["Home"].elo == 1600
        assert str(bundle.snapshots["Home"].last_game_date) == "2024-01-10"


class TestModelBundlePredict:
    def test_higher_home_elo_favored(self):
        bundle = _bundle({"Home": TeamSnapshot(elo=1700), "Away": TeamSnapshot(elo=1400)})
        p = bundle.predict_home_prob("Home", "Away")
        assert 0.0 <= p <= 1.0 and p > 0.5

    def test_participant_prob_flips_for_away(self):
        bundle = _bundle({"Home": TeamSnapshot(elo=1700), "Away": TeamSnapshot(elo=1400)})
        p_home = bundle.predict_participant_prob("Home", "Away", "Home", current_date=None)
        p_away = bundle.predict_participant_prob("Home", "Away", "Away", current_date=None)
        assert p_home + p_away == pytest.approx(1.0)

    def test_point_in_time_net_eff_shifts_probability(self):
        # net_rating_diff is now sourced from the snapshot's point-in-time net_eff.
        flat = _bundle({"Home": TeamSnapshot(elo=1500), "Away": TeamSnapshot(elo=1500)})
        strong = _bundle({"Home": TeamSnapshot(elo=1500, net_eff=12, season=2024),
                          "Away": TeamSnapshot(elo=1500, net_eff=-12, season=2024)})
        base = flat.predict_home_prob("Home", "Away", current_date=date(2025, 1, 1))
        boosted = strong.predict_home_prob("Home", "Away", current_date=date(2025, 1, 1))
        assert boosted > base

    def test_net_eff_reset_when_snapshot_is_prior_season(self):
        snaps = {"Home": TeamSnapshot(elo=1500, net_eff=12, season=2024),
                 "Away": TeamSnapshot(elo=1500, net_eff=-12, season=2024)}
        bundle = _bundle(snaps)
        same = bundle.predict_home_prob("Home", "Away", current_date=date(2025, 1, 1))  # season 2024
        later = bundle.predict_home_prob("Home", "Away", current_date=date(2026, 1, 1))  # season 2025 -> reset
        assert same > later  # stale-season net_eff is zeroed, removing the home edge

    def test_unknown_team_uses_neutral_prior(self):
        bundle = _bundle({})  # no snapshots -> both neutral -> ~ HFA only
        p = bundle.predict_home_prob("Ghost", "Phantom")
        assert 0.0 <= p <= 1.0

    def test_temperature_pulls_prediction_toward_half(self):
        snaps = {"Home": TeamSnapshot(elo=1750), "Away": TeamSnapshot(elo=1350)}
        hot = _bundle(snaps, _meta(temperature=1.0)).predict_home_prob("Home", "Away")
        warm = _bundle(snaps, _meta(temperature=2.5)).predict_home_prob("Home", "Away")
        assert 0.5 < warm < hot  # same model+inputs, T>1 is less confident

    def test_calibration_spec_overrides_legacy_temperature(self):
        # An isotonic spec mapping everything to 0.5 should flatten the output,
        # proving the serve path routes through meta["calibration"].
        snaps = {"Home": TeamSnapshot(elo=1750), "Away": TeamSnapshot(elo=1350)}
        spec = {"method": "isotonic", "x": [0.0, 1.0], "y": [0.5, 0.5]}
        p = _bundle(snaps, _meta(temperature=1.0, calibration=spec)).predict_home_prob("Home", "Away")
        assert p == pytest.approx(0.5, abs=1e-6)


class TestMonteCarlo:
    def test_strong_team_favored_and_probs_bounded(self):
        rng = np.random.default_rng(0)
        out = MonteCarloPricer(iterations=5000).simulate_game(120, 100, rng=rng)
        assert out["ml_prob"] > 0.6
        for key in ("ml_prob", "spread_prob", "over_prob"):
            assert 0.0 <= out[key] <= 1.0
