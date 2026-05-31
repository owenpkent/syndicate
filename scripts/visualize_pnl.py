"""Equity curve from REAL settled PnL.

Reads ``trades.pnl`` (written by the Settlement Agent, in stake-fraction units)
and compounds it into a bankroll curve. No more simulating a 55% win rate — if
there's no settled PnL yet, it says so.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sportsball.config import load_settings
from sportsball.db import Database

START_BANKROLL = 1000.0


def plot_pnl():
    db = Database(load_settings().db)
    rows = db.query(
        "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY settled_ts ASC NULLS LAST"
    )
    if not rows:
        print("No settled trades with PnL found. Run the pipeline (or 'make demo') and let "
              "the settlement agent run first.")
        return

    bankroll = [START_BANKROLL]
    for (pnl,) in rows:
        bankroll.append(bankroll[-1] * (1 + float(pnl)))

    plt.figure(figsize=(10, 6))
    plt.plot(bankroll, color="b", linewidth=2)
    plt.axhline(START_BANKROLL, color="red", linestyle="--", alpha=0.5)
    plt.title("Sportsball Equity Curve (realized PnL)")
    plt.xlabel("Settled trade #")
    plt.ylabel("Bankroll (units)")
    plt.grid(True, alpha=0.3)
    os.makedirs("data/plots", exist_ok=True)
    out = "data/plots/pnl_curve.png"
    plt.savefig(out)
    print(f"Equity curve ({len(rows)} settled trades) saved to {out}. "
          f"Final bankroll: {bankroll[-1]:.2f}")


if __name__ == "__main__":
    plot_pnl()
