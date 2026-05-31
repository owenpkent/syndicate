"""Model-quality harness: calibration + hyperparameter sweep (out-of-sample).

Two questions that matter for a betting model, answered on a chronological
holdout from the DuckDB history:

1. **Is P_true calibrated?** EV = P_true·odds − 1 and Kelly sizing both trust the
   probability *level*, not just ranking — a miscalibrated 0.6 that's really 0.55
   silently overstakes. We report Expected Calibration Error (ECE) + a reliability
   table, and compare the raw logistic against an isotonic-calibrated version.
2. **Are the fixed Elo/feature knobs optimal?** MOV, season carryover, and the
   form window are hardcoded defaults — we sweep them and report holdout log-loss.

    python scripts/model_quality.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.pipelines._elo import walk_forward  # noqa: E402
from sportsball.quant import features as feat  # noqa: E402

import measure_features as mf  # noqa: E402  (sibling: team_maps_from_pg)
import train_eval_duckdb as ted  # noqa: E402  (sibling: load_events, HFA, K)

SPLIT = 0.85


def expected_calibration_error(p: np.ndarray, y: np.ndarray, bins: int = 10):
    """ECE + a reliability table [(lo, hi, n, mean_pred, actual_rate)]."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece, table = 0.0, []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        mean_pred, actual = float(p[mask].mean()), float(y[mask].mean())
        ece += (n / len(p)) * abs(mean_pred - actual)
        table.append((lo, hi, n, mean_pred, actual))
    return ece, table


def _xy(rows_raw, roster_pit, *, mov, carry, form_window):
    frows, _ = walk_forward(rows_raw, ted.K, ted.HFA, mov_enabled=mov, carry=carry,
                            gap_days=90, form_window=form_window, roster_pit=roster_pit)
    X = np.array([r.features for r in frows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in frows])
    return X, y


def _split(X, y):
    cut = int(len(X) * SPLIT)
    return X[:cut], y[:cut], X[cut:], y[cut:]


def _pipe():
    return Pipeline([("scaler", StandardScaler()), ("lr", LogisticRegression(max_iter=1000))])


def main() -> None:
    src = ted.DEFAULT_DB
    if not Path(src).exists():
        print(f"DuckDB {src} not found.")
        return
    tmp = Path(tempfile.gettempdir()) / "model_quality.duckdb"
    shutil.copy(src, tmp)
    rows_raw = ted.load_events(str(tmp))
    tmp.unlink(missing_ok=True)
    roster_pit = mf.roster_pit_from_pg()
    print(f"Loaded {len(rows_raw)} games.\n")

    # --- 1. Calibration: raw logistic vs isotonic --------------------------
    X, y = _xy(rows_raw, roster_pit, mov=True, carry=0.75, form_window=10)
    Xtr, ytr, Xte, yte = _split(X, y)

    raw = _pipe().fit(Xtr, ytr)
    p_raw = raw.predict_proba(Xte)[:, 1]
    iso = CalibratedClassifierCV(_pipe(), method="isotonic", cv=3).fit(Xtr, ytr)
    p_iso = iso.predict_proba(Xte)[:, 1]

    ece_raw, table = expected_calibration_error(p_raw, yte)
    ece_iso, _ = expected_calibration_error(p_iso, yte)

    print("=== Calibration (holdout) ===")
    print(f"{'model':<12}{'brier':>9}{'log_loss':>11}{'ECE':>9}")
    for name, p in (("raw", p_raw), ("isotonic", p_iso)):
        print(f"{name:<12}{brier_score_loss(yte, p):>9.4f}"
              f"{log_loss(yte, p, labels=[0,1]):>11.4f}"
              f"{(ece_raw if name=='raw' else ece_iso):>9.4f}")

    print("\nReliability (raw): bin    n   pred  actual")
    for lo, hi, n, pred, act in table:
        print(f"  {lo:.1f}-{hi:.1f}  {n:>5}  {pred:.3f}  {act:.3f}")

    # --- 2. Hyperparameter sweep (holdout log-loss) ------------------------
    print("\n=== Elo/feature sweep (holdout log_loss) ===")
    print(f"{'mov':>5}{'carry':>7}{'form_w':>8}{'log_loss':>11}")
    best = None
    for mov in (True, False):
        for carry in (0.6, 0.75, 0.9):
            for fw in (5, 10, 20):
                Xs, ys = _xy(rows_raw, roster_pit, mov=mov, carry=carry, form_window=fw)
                a, b, c, d = _split(Xs, ys)
                m = _pipe().fit(a, b)
                ll = log_loss(d, m.predict_proba(c)[:, 1], labels=[0, 1])
                if best is None or ll < best[-1]:
                    best = (mov, carry, fw, ll)
                print(f"{str(mov):>5}{carry:>7}{fw:>8}{ll:>11.4f}")
    print(f"\nbest: mov={best[0]} carry={best[1]} form_window={best[2]} "
          f"log_loss={best[3]:.4f}  (current default: mov=True carry=0.75 form_window=10)")


if __name__ == "__main__":
    main()
