"""Measure the v4 algorithm changes on a complete SYNTHETIC season — no data needed.

Quantifies, out-of-sample (chronological holdout), the lift from each recent
algorithm change so they're not just "plumbed and tested" but *measured*:

  1. Feature lift     — Elo-only -> +schedule -> +availability -> +market (logistic).
  2. Ensemble         — logistic vs logistic+GBM on the full feature set.
  3. Calibration      — raw vs temperature vs isotonic vs auto (log-loss / Brier).
  4. Uncertainty-Kelly— the calibration-confidence stake-shrink factor.

Everything is SYNTHETIC (the market signal is an efficient estimate of the true
win prob by construction, so its lift is an upper bound) — this validates that the
machinery captures the signal, not that any real edge exists.

    python scripts/measure_algorithms.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import train_eval_duckdb as ted  # noqa: E402  (real logistic holdout metrics)
from sportsball.pipelines._elo import walk_forward  # noqa: E402
from sportsball.pipelines.train import _build_model, _logistic  # noqa: E402
from sportsball.quant import calibration as cal  # noqa: E402
from sportsball.quant import features as feat  # noqa: E402

from synth import make_season  # noqa: E402

K, HFA, SPLIT = 22.0, 55.0, 0.8
IDX = {name: i for i, name in enumerate(feat.FEATURE_ORDER)}


def _metrics(yte, p) -> dict:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"brier": float(brier_score_loss(yte, p)),
            "log_loss": float(log_loss(yte, p, labels=[0, 1])),
            "accuracy": float(accuracy_score(yte, (p >= 0.5).astype(int)))}


def _row(name, m, prev_ll):
    delta = "" if prev_ll is None else f"{prev_ll - m['log_loss']:+.4f}"
    print(f"{name:<30}{m['brier']:>9.4f}{m['log_loss']:>11.4f}{m['accuracy']:>10.4f}{delta:>11}")


def main() -> None:
    rng = np.random.default_rng(0)
    results, availability_pit, market_pit = make_season(
        rng, n_teams=14, n_games=6000, with_market=True)
    rows, _ = walk_forward(results, K, HFA, mov_enabled=True, carry=0.75, gap_days=90,
                           form_window=10, availability_pit=availability_pit,
                           market_pit=market_pit)
    X = np.array([r.features for r in rows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in rows])
    cut = int(len(X) * SPLIT)
    print(f"[SYNTHETIC] {len(X)} games | train {cut} | holdout {len(X) - cut}\n")

    # 1. Feature lift (logistic), adding groups in order.
    print("1) FEATURE LIFT (logistic, chronological holdout)")
    print(f"{'feature set':<30}{'brier':>9}{'log_loss':>11}{'accuracy':>10}{'Δlog-loss':>11}")
    sched = [IDX[n] for n in ("elo_diff_hfa", "net_rating_diff", "rest_diff",
                              "b2b_home", "b2b_away", "form_diff", "player_strength_diff")]
    stages = [
        ("Elo-only (1 feat)", [0]),
        ("+ schedule/form (7)", sched),
        ("+ availability (8)", sched + [IDX["availability_diff"]]),
        ("+ market  (9, full)", sched + [IDX["availability_diff"], IDX["market_logit"]]),
    ]
    prev = None
    full_logit = None
    for name, cols in stages:
        m = ted.holdout_metrics(X, y, cols=cols, split=SPLIT)
        m = {"brier": m["brier"], "log_loss": m["log_loss"], "accuracy": m["accuracy"]}
        _row(name, m, prev)
        prev, full_logit = m["log_loss"], m

    # 2. Ensemble vs logistic (full feature set).
    print("\n2) ENSEMBLE (full 9 features)")
    print(f"{'model':<30}{'brier':>9}{'log_loss':>11}{'accuracy':>10}{'Δlog-loss':>11}")
    _row("logistic", full_logit, None)
    ens = _build_model(X[:cut], y[:cut], ensemble=True)
    em = _metrics(y[cut:], ens.predict_proba(X[cut:])[:, 1])
    _row("logistic + GBM ensemble", em, full_logit["log_loss"])

    # 3. Calibration: fit on a train tail, apply to the holdout.
    print("\n3) CALIBRATION (full logistic; spec fit on a train tail, applied to holdout)")
    print(f"{'method':<30}{'brier':>9}{'log_loss':>11}")
    base = _logistic().fit(X[:cut], y[:cut])
    p_test = base.predict_proba(X[cut:])[:, 1]
    ccut = int(cut * 0.85)
    probe = _logistic().fit(X[:ccut], y[:ccut])
    p_cal, y_cal = probe.predict_proba(X[ccut:cut])[:, 1], y[ccut:cut]
    specs = {
        "raw (identity)": {"method": "identity"},
        "temperature": cal.fit(p_cal, y_cal, method="temperature"),
        "isotonic": cal.fit(p_cal, y_cal, method="isotonic"),
        "auto (selected)": cal.fit(p_cal, y_cal, method="auto"),
    }
    auto_spec = specs["auto (selected)"]
    for name, spec in specs.items():
        m = _metrics(y[cut:], cal.apply(p_test, spec))
        tag = f"  <- {auto_spec.get('method')}" if name == "auto (selected)" else ""
        print(f"{name:<30}{m['brier']:>9.4f}{m['log_loss']:>11.4f}{tag}")

    # 4. Uncertainty-aware Kelly: the confidence stake-shrink from the chosen spec.
    conf = cal.confidence(auto_spec)
    print("\n4) UNCERTAINTY-AWARE KELLY")
    print(f"calibration spec: {auto_spec.get('method')} | confidence factor: {conf:.3f}")
    print(f"base quarter-Kelly 0.2500 -> effective {0.25 * conf:.4f} "
          f"({'shrunk' if conf < 1 else 'unchanged'} by the model's (un)certainty)")

    print("\n(SYNTHETIC: the market feature is an efficient estimate of the true prob, "
          "so its lift is an upper bound; real lift needs real closing odds.)")


if __name__ == "__main__":
    main()
