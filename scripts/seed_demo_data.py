import psycopg2
import os
import random
from datetime import datetime, timedelta

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="syndicate_admin",
        password="changeme_in_env"
    )

def seed():
    print("Seeding database with 500 matched records for visualization...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Clear existing for a clean demo if desired, 
            # but usually better to just append
            
            for i in range(500):
                event_id = f"SEED-{i:03}"
                # Generate a "True Probability"
                true_prob = random.random()
                # Simulate "Actual Outcome" based on that probability
                outcome = 1 if random.random() < true_prob else 0
                # Generate "Market Odds" (with some noise/discrepancy)
                # If we're "good", our true_prob should correlate with outcome
                odds = round(1 / (true_prob + random.uniform(-0.1, 0.1)), 2)
                if odds <= 1: odds = 2.0
                
                market_id = f"RUNDOWN-{event_id}-TeamA"
                
                # Insert into market_history
                cur.execute(
                    "INSERT INTO market_history (market_id, odds, true_prob, ev) VALUES (%s, %s, %s, %s)",
                    (market_id, odds, true_prob, (true_prob * odds) - 1)
                )
                
                # Insert into historical_results
                h_score = 110 if outcome == 1 else 100
                a_score = 100 if outcome == 1 else 110
                cur.execute(
                    "INSERT INTO historical_results (event_id, home_team, away_team, home_score, away_score, home_odds, away_odds) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (event_id, "TeamA", "TeamB", h_score, a_score, odds, 1.91)
                )

                # Insert into trade_history (Simulation of executed trades)
                if (true_prob * odds) - 1 > 0.05: # High EV trades
                    cur.execute(
                        "INSERT INTO trade_history (market_id, executed_odds, fraction, status) VALUES (%s, %s, %s, %s)",
                        (market_id, odds, 0.02, "WIN" if outcome == 1 else "LOSS")
                    )

            conn.commit()
            print("Successfully seeded 500 matched records.")
    finally:
        conn.close()

if __name__ == "__main__":
    seed()
