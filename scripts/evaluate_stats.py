import psycopg2
import os
import numpy as np
from sklearn.metrics import log_loss, brier_score_loss, mean_squared_error

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def evaluate_model():
    """
    Calculates professional scoring metrics for the model's 'true_prob' predictions.
    """
    print("--- Professional Model Evaluation ---")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Get matched signals and outcomes
            cur.execute("""
                SELECT mh.true_prob, 
                       CASE WHEN hr.home_score > hr.away_score THEN 1 ELSE 0 END as actual_home,
                       hr.home_team
                FROM market_history mh
                JOIN historical_results hr ON mh.market_id LIKE '%' || hr.event_id || '%'
                WHERE hr.home_score IS NOT NULL
            """)
            data = cur.fetchall()
            
            if not data:
                print("No matched history found. Run 'make demo' first.")
                return

            # Note: We need to adjust true_prob if the market_id was for the away team
            # For this evaluation, we'll assume the joined true_prob is for the home side 
            # (or we'd need more complex logic in the join)
            # In our demo seeder, market_id is 'RUNDOWN-EVENTID-TeamA' (home side)
            
            y_pred = np.array([float(d[0]) for d in data])
            y_true = np.array([d[1] for d in data])

            # 1. Brier Score (MSE for probabilities: closer to 0 is better)
            brier = brier_score_loss(y_true, y_pred)
            
            # 2. Log Loss (Penalty for wrong confidence: closer to 0 is better)
            # Clip to avoid infinity
            ll = log_loss(y_true, y_pred, labels=[0, 1])
            
            # 3. RMSE (Standard error)
            rmse = np.sqrt(mean_squared_error(y_true, y_pred))

            print(f"Total Samples evaluated: {len(y_true)}")
            print("-" * 30)
            print(f"Brier Score: {brier:.4f}  (Professional benchmark < 0.25)")
            print(f"Log-Loss:    {ll:.4f}  (Perfect coin flip is 0.693)")
            print(f"RMSE:        {rmse:.4f}")
            print("-" * 30)
            
            if brier < 0.22:
                print("VERDICT: HIGHLY ACCURATE MODEL")
            elif brier < 0.25:
                print("VERDICT: COMPETITIVE MODEL (Betting Edge Possible)")
            else:
                print("VERDICT: POOR CALIBRATION (Check your features/Elo)")

    finally:
        conn.close()

if __name__ == "__main__":
    evaluate_model()
