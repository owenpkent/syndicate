"""Professional model evaluation: Brier score, log-loss, RMSE.

Scores the Engine's modeled ``true_prob`` against realized outcomes. Each signal
already records which ``side`` it priced, so the true label is unambiguous — no
home/away guessing as in the original.
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import brier_score_loss, log_loss, mean_squared_error

from ..config import load_settings
from ..db import Database
from ..store import HOME, Store


def evaluate(store: Store) -> dict | None:
    rows = store.signal_outcome_rows()
    if not rows:
        return None
    y_pred, y_true = [], []
    for true_prob, side, home_score, away_score in rows:
        won = (side == HOME and home_score > away_score) or (side != HOME and away_score > home_score)
        y_pred.append(float(true_prob))
        y_true.append(1 if won else 0)
    y_pred, y_true = np.array(y_pred), np.array(y_true)
    return {
        "n": len(y_true),
        "brier": float(brier_score_loss(y_true, y_pred)),
        "log_loss": float(log_loss(y_true, y_pred, labels=[0, 1])),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
    }


def main() -> None:
    print("--- Professional Model Evaluation ---")
    result = evaluate(Store(Database(load_settings().db)))
    if not result:
        print("No matched signals on FINAL events. Run 'make demo' or backfill + train first.")
        return
    print(f"Samples evaluated: {result['n']}")
    print("-" * 32)
    print(f"Brier Score: {result['brier']:.4f}  (benchmark < 0.25)")
    print(f"Log-Loss:    {result['log_loss']:.4f}  (coin flip = 0.693)")
    print(f"RMSE:        {result['rmse']:.4f}")
    print("-" * 32)
    if result["brier"] < 0.22:
        print("VERDICT: HIGHLY ACCURATE MODEL")
    elif result["brier"] < 0.25:
        print("VERDICT: COMPETITIVE MODEL (betting edge possible)")
    else:
        print("VERDICT: POOR CALIBRATION (check features / Elo)")


if __name__ == "__main__":
    main()
