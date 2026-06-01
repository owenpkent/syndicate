"""Probability calibration — temperature, isotonic, or auto-selected.

The logistic is systematically over-confident out-of-sample. v2 fixed the *scale*
with a single temperature parameter; this generalizes it: fit a calibrator on a
held-out tail and persist a small **spec** the serve path applies purely (numpy
only), so ``ModelBundle`` never needs sklearn to calibrate.

Specs (all JSON-serializable, stored in ``model_meta.json``):
- ``{"method": "identity"}`` — no-op (too little data to calibrate).
- ``{"method": "temperature", "temperature": T}`` — divide the logit by ``T``.
- ``{"method": "isotonic", "x": [...], "y": [...]}`` — monotonic piecewise-linear
  map (the sklearn IsotonicRegression knots), applied with ``np.interp``.

``method="auto"`` fits both on one half of the tail, scores log-loss on the other,
and keeps the winner (refit on the full tail) — so isotonic is only chosen when it
*generalizes* better, not merely fits in-sample.
"""
from __future__ import annotations

import numpy as np

from .features import temperature_scale

_EPS = 1e-6


def _clip(p):
    return np.clip(p, _EPS, 1 - _EPS)


def _log_loss(y, p) -> float:
    p = _clip(p)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_temperature(p, y) -> float:
    """Temperature that minimizes log-loss of ``sigmoid(logit(p)/T)`` on (p, y)."""
    from scipy.optimize import minimize_scalar
    logit = np.log(_clip(p) / (1 - _clip(p)))
    y = np.asarray(y, dtype=float)
    res = minimize_scalar(lambda T: _log_loss(y, 1 / (1 + np.exp(-logit / T))),
                          bounds=(0.3, 5.0), method="bounded")
    return float(res.x) if res.success else 1.0


def fit_isotonic(p, y) -> tuple[list, list]:
    """Fit isotonic regression; return its (x, y) interpolation knots."""
    from sklearn.isotonic import IsotonicRegression
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    ir.fit(np.asarray(p, dtype=float), np.asarray(y, dtype=float))
    return ir.X_thresholds_.tolist(), ir.y_thresholds_.tolist()


def apply(p, spec: dict | None):
    """Apply a calibration spec to a probability (scalar or array). Pure / numpy."""
    if not spec or spec.get("method") in (None, "identity"):
        return p
    method = spec["method"]
    if method == "temperature":
        T = float(spec.get("temperature", 1.0))
        if np.isscalar(p):
            return temperature_scale(float(p), T)
        pc = _clip(np.asarray(p, dtype=float))
        return 1 / (1 + np.exp(-np.log(pc / (1 - pc)) / T))
    if method == "isotonic":
        xs, ys = np.asarray(spec["x"], dtype=float), np.asarray(spec["y"], dtype=float)
        out = np.interp(p, xs, ys)  # np.interp clips to the endpoints by default
        return float(out) if np.isscalar(p) else out
    return p


def confidence(spec: dict | None, *, ref: float = 0.8, floor: float = 0.25) -> float:
    """Stake-shrink factor in ``[floor, 1]`` from how much the calibrator tempers a
    confident prediction.

    Measures the share of a ``ref`` (e.g. 0.8) prediction's edge over 0.5 that the
    calibrator keeps: ``(apply(ref) - 0.5) / (ref - 0.5)``. A well-calibrated model
    keeps ~all of it (→ 1.0, full stake); a heavily over-confident one that the
    calibrator shrinks keeps less (→ smaller, stake less). This is the
    research-backed "size down when the estimate is noisy" lever, derived uniformly
    from any spec (temperature or isotonic). ``identity``/``None`` → 1.0.
    """
    if not spec or spec.get("method") in (None, "identity"):
        return 1.0
    kept = (float(apply(ref, spec)) - 0.5) / (ref - 0.5)
    return float(max(floor, min(1.0, kept)))


def fit(p, y, *, method: str = "auto", min_n: int = 100) -> dict:
    """Fit a calibrator on (p, y) and return a JSON-serializable spec.

    ``method`` ∈ {auto, temperature, isotonic, identity}. With fewer than
    ``min_n`` points returns ``identity`` (calibrating on a handful of games is
    noise). ``auto`` picks the method that generalizes better out-of-fold.
    """
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(p) < min_n or method == "identity":
        return {"method": "identity"}
    if method == "temperature":
        return {"method": "temperature", "temperature": fit_temperature(p, y)}
    if method == "isotonic":
        xs, ys = fit_isotonic(p, y)
        return {"method": "isotonic", "x": xs, "y": ys}

    # auto: out-of-fold selection between temperature and isotonic.
    cut = len(p) // 2
    if cut < min_n // 2:
        return {"method": "temperature", "temperature": fit_temperature(p, y)}
    pa, ya, pb, yb = p[:cut], y[:cut], p[cut:], y[cut:]
    t_spec = {"method": "temperature", "temperature": fit_temperature(pa, ya)}
    candidates = [t_spec]
    try:
        xs, ys = fit_isotonic(pa, ya)
        candidates.append({"method": "isotonic", "x": xs, "y": ys})
    except Exception:  # noqa: BLE001 - degenerate input; temperature is the fallback
        pass
    baseline = _log_loss(yb, pb)
    best, best_ll = {"method": "identity"}, baseline
    for spec in candidates:
        ll = _log_loss(yb, apply(pb, spec))
        if ll < best_ll:
            best, best_ll = spec, ll
    # refit the winner on the full tail
    if best["method"] == "temperature":
        return {"method": "temperature", "temperature": fit_temperature(p, y)}
    if best["method"] == "isotonic":
        xs, ys = fit_isotonic(p, y)
        return {"method": "isotonic", "x": xs, "y": ys}
    return best
