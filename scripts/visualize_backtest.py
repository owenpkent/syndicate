"""Walk-forward backtest visualization over FINAL events.

Replays the trained model game-by-game (updating Elo as it goes), trading any
side with EV above the buffer, and plots the resulting equity curve. Reuses the
package's quant primitives and the shared Elo walk-forward so the math matches
the live Engine.
"""
import json
import os
import pickle
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sportsball.config import load_settings
from sportsball.db import Database
from sportsball.quant.odds import calculate_ev, calculate_kelly_fraction

START_BANKROLL = 1000.0
EV_BUFFER = 0.02


def run_historical_viz():
    try:
        params = json.loads(Path("optimized_params.json").read_text())
        model = pickle.loads(Path("models/win_prob_model.pkl").read_bytes())
        k_factor, hfa = params["k_factor"], params["hfa"]
        print(f"Loaded model: K={k_factor:.2f}, HFA={hfa:.2f}")
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading model artifacts: {exc}. Run 'make optimize' then 'make train'.")
        return

    db = Database(load_settings().db)
    results = db.query(
        """
        SELECT home_team, away_team, home_score, away_score, home_close, away_close
        FROM events
        WHERE status = 'FINAL' AND home_close > 0 AND away_close > 0
        ORDER BY event_date ASC
        """
    )
    if not results:
        print("No FINAL events with closing odds. Run a backfill (or 'make demo') first.")
        return

    bankroll = START_BANKROLL
    equity = [bankroll]
    ratings: dict[str, float] = {}
    trades = 0

    for home, away, h_score, a_score, h_odds, a_odds in results:
        r_home, r_away = ratings.get(home, 1500), ratings.get(away, 1500)
        p_home = model.predict_proba([[(r_home + hfa) - r_away]])[0][1]
        ev_home, ev_away = calculate_ev(p_home, float(h_odds)), calculate_ev(1 - p_home, float(a_odds))

        if ev_home > EV_BUFFER:
            risk = bankroll * calculate_kelly_fraction(ev_home, float(h_odds))
            bankroll += risk * (float(h_odds) - 1) if h_score > a_score else -risk
            trades += 1
        elif ev_away > EV_BUFFER:
            risk = bankroll * calculate_kelly_fraction(ev_away, float(a_odds))
            bankroll += risk * (float(a_odds) - 1) if a_score > h_score else -risk
            trades += 1
        equity.append(bankroll)

        actual_home = 1 if h_score > a_score else 0
        exp_home = 1 / (1 + 10 ** ((r_away - (r_home + hfa)) / 400))
        shift = k_factor * (actual_home - exp_home)
        ratings[home], ratings[away] = r_home + shift, r_away - shift

    plt.figure(figsize=(12, 6))
    plt.plot(equity, color="green", linewidth=2)
    plt.axhline(START_BANKROLL, color="red", linestyle="--", alpha=0.5)
    plt.title(f"Walk-Forward Backtest ({len(results)} games)")
    plt.xlabel("Game sequence")
    plt.ylabel("Bankroll (units)")
    plt.grid(True, alpha=0.3)
    os.makedirs("data/plots", exist_ok=True)
    out = "data/plots/backtest_performance.png"
    plt.savefig(out)
    print("-" * 30)
    print(f"Trades: {trades} | Final bankroll: {bankroll:.2f} | "
          f"ROI: {(bankroll / START_BANKROLL - 1) * 100:.2f}% | saved {out}")


if __name__ == "__main__":
    run_historical_viz()
