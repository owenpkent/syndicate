"""Probability calibration: temperature, isotonic, auto-selection, serve apply."""
import numpy as np
import pytest

from sportsball.quant import calibration as cal


def _miscalibrated(n=4000, seed=0):
    """Over-confident probabilities: raw p is sharper than the true rate."""
    rng = np.random.default_rng(seed)
    true_p = rng.uniform(0.2, 0.8, n)
    y = (rng.uniform(size=n) < true_p).astype(float)
    # push raw predictions away from 0.5 (classic over-confidence)
    logit = np.log(true_p / (1 - true_p)) * 1.8
    raw = 1 / (1 + np.exp(-logit))
    return raw, y, true_p


def _ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


class TestApply:
    def test_identity_and_none(self):
        assert cal.apply(0.7, None) == 0.7
        assert cal.apply(0.7, {"method": "identity"}) == 0.7

    def test_temperature_shrinks_toward_half(self):
        out = cal.apply(0.9, {"method": "temperature", "temperature": 2.0})
        assert 0.5 < out < 0.9

    def test_isotonic_is_monotonic_and_clipped(self):
        spec = {"method": "isotonic", "x": [0.0, 0.5, 1.0], "y": [0.1, 0.5, 0.9]}
        assert cal.apply(0.0, spec) == pytest.approx(0.1)
        assert cal.apply(1.0, spec) == pytest.approx(0.9)
        assert cal.apply(0.25, spec) < cal.apply(0.75, spec)

    def test_apply_handles_arrays(self):
        out = cal.apply(np.array([0.2, 0.8]), {"method": "temperature", "temperature": 2.0})
        assert out.shape == (2,) and out[0] < 0.5 < out[1]


class TestFit:
    def test_too_little_data_is_identity(self):
        assert cal.fit([0.6] * 10, [1] * 10)["method"] == "identity"

    def test_temperature_method_returns_T(self):
        raw, y, _ = _miscalibrated()
        spec = cal.fit(raw, y, method="temperature")
        assert spec["method"] == "temperature"
        assert spec["temperature"] > 1.0  # over-confident -> T>1

    def test_calibration_reduces_log_loss(self):
        raw, y, _ = _miscalibrated()
        spec = cal.fit(raw, y, method="auto")
        assert _ll(y, cal.apply(raw, spec)) < _ll(y, raw)

    def test_auto_picks_a_real_method_on_signal(self):
        raw, y, _ = _miscalibrated()
        assert cal.fit(raw, y, method="auto")["method"] in ("temperature", "isotonic")

    def test_isotonic_spec_is_json_native(self):
        import json
        raw, y, _ = _miscalibrated()
        json.dumps(cal.fit(raw, y, method="isotonic"))
