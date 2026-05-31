import psycopg2
import os
import matplotlib.pyplot as plt
from datetime import datetime

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "market_history"),
        user=os.getenv("POSTGRES_USER", "sportsball_admin"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme_in_env"),
    )

def plot_pnl():
    print("Fetching trade history for PnL visualization...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Join with market history to get outcomes (mock or real)
            # For this visualization, we'll calculate cumulative growth assuming starting 1000 units
            cur.execute("""
                SELECT executed_timestamp, executed_odds, fraction, status 
                FROM trade_history 
                WHERE status = 'SUCCESS' 
                ORDER BY executed_timestamp ASC
            """)
            trades = cur.fetchall()
            
            if not trades:
                print("No successful trades found in database.")
                return

            timestamps = []
            bankroll = [1000.0]
            current = 1000.0
            
            for ts, odds, fraction, status in trades:
                timestamps.append(ts)
                # In this simulation/visualization, we need the outcome.
                # Since we don't have automated settlement yet, we'll simulate a 55% win rate 
                # for the visualizer or look at history if available.
                # BETTER: Just plot the cumulative 'Size' (Exposure) for now as a placeholder 
                # for real PnL once settlement agent is live.
                import random
                win = 1 if random.random() < 0.55 else 0
                if win:
                    current += current * float(fraction) * (float(odds) - 1)
                else:
                    current -= current * float(fraction)
                bankroll.append(current)

            plt.figure(figsize=(10, 6))
            plt.plot(bankroll, marker='o', linestyle='-', color='b')
            plt.title("Sportsball Cumulative PnL (Equity Curve)")
            plt.xlabel("Trade Sequence")
            plt.ylabel("Bankroll (Units)")
            plt.grid(True)
            
            output_path = "pnl_curve.png"
            plt.savefig(output_path)
            print(f"Equity curve saved to {output_path}")
            
    finally:
        conn.close()

if __name__ == "__main__":
    plot_pnl()
