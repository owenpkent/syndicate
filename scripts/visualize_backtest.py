import psycopg2
import os
import json
import pickle
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def calculate_ev(true_prob, odds):
    return (true_prob * odds) - 1

def calculate_kelly(ev, odds, multiplier=0.25):
    if odds <= 1: return 0
    return multiplier * max(0, ev / (odds - 1))

def run_historical_viz():
    print("Starting Historical Performance Visualization...")
    
    # 1. Load Parameters and Model
    try:
        with open("src/analytics_engine/optimized_params.json", "r") as f:
            params = json.load(f)
            k_factor = params["k_factor"]
            hfa = params["hfa"]
        with open("src/analytics_engine/models/win_prob_model.pkl", "rb") as f:
            model = pickle.load(f)
        print(f"Loaded Models: K={k_factor:.2f}, HFA={hfa:.2f}")
    except Exception as e:
        print(f"Error loading model artifacts: {e}. Did you run 'make setup' and the trainer?")
        return

    # 2. Fetch Data
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT home_team, away_team, home_score, away_score, home_odds, away_odds, event_date
                FROM historical_results 
                WHERE home_odds > 0 AND away_odds > 0
                ORDER BY event_date ASC
            """)
            results = cur.fetchall()
            
        if not results:
            print("No historical data found with odds. Run a backfill first.")
            return

        # 3. Simulation Loop
        bankroll = 1000.0
        equity_curve = [bankroll]
        ratings = {}
        trade_count = 0
        
        for h_team, a_team, h_score, a_score, h_odds, a_odds, dt in results:
            # Current Ratings
            r_home = ratings.get(h_team, 1500)
            r_away = ratings.get(a_team, 1500)
            
            # Predict Probabilities
            diff = (r_home + hfa) - r_away
            p_home = model.predict_proba([[diff]])[0][1]
            p_away = 1 - p_home
            
            # Check for EV
            ev_home = calculate_ev(p_home, float(h_odds))
            ev_away = calculate_ev(p_away, float(a_odds))
            
            # Execute Trade logic (Simple: pick highest EV > 0.02)
            if ev_home > 0.02:
                fraction = calculate_kelly(ev_home, float(h_odds))
                risk = bankroll * fraction
                if h_score > a_score:
                    bankroll += risk * (float(h_odds) - 1)
                else:
                    bankroll -= risk
                trade_count += 1
            elif ev_away > 0.02:
                fraction = calculate_kelly(ev_away, float(a_odds))
                risk = bankroll * fraction
                if a_score > h_score:
                    bankroll += risk * (float(a_odds) - 1)
                else:
                    bankroll -= risk
                trade_count += 1
                
            equity_curve.append(bankroll)
            
            # Update ratings (Elo logic)
            actual_home = 1 if h_score > a_score else 0
            # Exp prob for elo update (uses raw elo formula)
            exp_home_elo = 1 / (1 + 10 ** ((r_away - (r_home + hfa)) / 400))
            shift = k_factor * (actual_home - exp_home_elo)
            ratings[h_team] = r_home + shift
            ratings[a_team] = r_away - shift

        # 4. Plotting
        plt.figure(figsize=(12, 6))
        plt.plot(equity_curve, color='green', linewidth=2)
        plt.title(f"Historical Walk-Forward Backtest ({len(results)} Games)")
        plt.xlabel("Game Sequence")
        plt.ylabel("Bankroll ($)")
        plt.grid(True, alpha=0.3)
        plt.axhline(y=1000, color='red', linestyle='--', alpha=0.5) # Starting line
        
        output_path = "data/plots/backtest_performance.png"
        os.makedirs("data/plots", exist_ok=True)
        plt.savefig(output_path)
        
        print("-" * 30)
        print(f"Simulation Complete!")
        print(f"Total Trades:   {trade_count}")
        print(f"Final Bankroll: ${bankroll:.2f}")
        print(f"Total ROI:      {((bankroll/1000 - 1) * 100):.2f}%")
        print(f"Chart saved to: {output_path}")

    finally:
        conn.close()

if __name__ == "__main__":
    run_historical_viz()
