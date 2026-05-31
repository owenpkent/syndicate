"""Calibration plot: predicted win probability vs realized win rate.

Uses ``signals`` joined to FINAL ``events`` (via the repository), grading each
signal against the side it actually priced — so the diagonal means "well
calibrated" with no home/away ambiguity.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from sportsball.config import load_settings
from sportsball.db import Database
from sportsball.store import HOME, Store


def plot_calibration():
    store = Store(Database(load_settings().db))
    rows = store.signal_outcome_rows()
    if not rows:
        print("Not enough matched history to generate calibration. Run 'make demo' first.")
        return

    probs, actuals = [], []
    for true_prob, side, home_score, away_score in rows:
        won = (side == HOME and home_score > away_score) or (side != HOME and away_score > home_score)
        probs.append(float(true_prob))
        actuals.append(1 if won else 0)
    probs, actuals = np.array(probs), np.array(actuals)

    bins = np.linspace(0, 1, 11)
    idx = np.digitize(probs, bins) - 1
    bin_pred, bin_actual = [], []
    for i in range(len(bins) - 1):
        mask = idx == i
        if np.any(mask):
            bin_pred.append(probs[mask].mean())
            bin_actual.append(actuals[mask].mean())

    plt.figure(figsize=(8, 8))
    plt.plot([0, 1], [0, 1], "k--", label="Perfect calibration")
    plt.plot(bin_pred, bin_actual, "s-", label="Sportsball model")
    plt.title("Model Calibration: predicted vs actual win rate")
    plt.xlabel("Predicted win probability")
    plt.ylabel("Actual win rate")
    plt.legend()
    plt.grid(True)
    os.makedirs("data/plots", exist_ok=True)
    out = "data/plots/calibration_plot.png"
    plt.savefig(out)
    print(f"Calibration plot ({len(probs)} signals) saved to {out}")


if __name__ == "__main__":
    plot_calibration()
