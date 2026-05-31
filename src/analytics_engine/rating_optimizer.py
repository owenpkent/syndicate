import os
import psycopg2
import numpy as np
from scipy.optimize import minimize
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RatingOptimizer")

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def fetch_historical_data():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT event_date, home_team, away_team, home_score, away_score 
                FROM historical_results 
                ORDER BY event_date ASC
            """)
            return cur.fetchall()
    finally:
        conn.close()

def simulate_elo(results, k_factor, hfa):
    """
    Simulates Elo ratings over a sequence of results.
    Returns the total log-loss of the predictions.
    """
    ratings = {} # team_name -> rating
    total_log_loss = 0
    predictions_count = 0
    
    for _, home_team, away_team, home_score, away_score in results:
        # Initialize ratings if new
        r_home = ratings.get(home_team, 1500)
        r_away = ratings.get(away_team, 1500)
        
        # Calculate expected outcome for Home (adjusted for HFA)
        r_home_adj = r_home + hfa
        exp_home = 1 / (1 + 10 ** ((r_away - r_home_adj) / 400))
        
        # Actual outcome (1 for home win, 0.5 for draw, 0 for away win)
        # Basketball doesn't have draws usually
        if home_score > away_score:
            actual = 1
        elif home_score < away_score:
            actual = 0
        else:
            actual = 0.5
            
        # Calculate Log-Loss
        # Clip probabilities to avoid log(0)
        p = max(min(exp_home, 0.999), 0.001)
        log_loss = -(actual * np.log(p) + (1 - actual) * np.log(1 - p))
        total_log_loss += log_loss
        predictions_count += 1
        
        # Update ratings
        shift = k_factor * (actual - exp_home)
        ratings[home_team] = r_home + shift
        ratings[away_team] = r_away - shift
        
    return total_log_loss / predictions_count if predictions_count > 0 else 1.0

def objective(params, results):
    k_factor, hfa = params
    return simulate_elo(results, k_factor, hfa)

def main():
    logger.info("Fetching historical data for optimization...")
    results = fetch_historical_data()
    
    if not results:
        logger.error("No historical data found. Please run the backfill first.")
        return
        
    logger.info(f"Optimizing over {len(results)} games...")
    
    # Initial guess [K, HFA]
    initial_guess = [20.0, 50.0]
    bounds = [(5, 100), (0, 200)]
    
    res = minimize(objective, initial_guess, args=(results,), method='L-BFGS-B', bounds=bounds)
    
    if res.success:
        opt_k, opt_hfa = res.x
        logger.info(f"Optimization Successful!")
        logger.info(f"Optimal K-Factor: {opt_k:.2f}")
        logger.info(f"Optimal Home-Field Advantage: {opt_hfa:.2f}")
        logger.info(f"Minimum Log-Loss: {res.fun:.4f}")
        
        # Save results to a file or DB for the trainer to use
        with open("optimized_params.json", "w") as f:
            import json
            json.dump({"k_factor": opt_k, "hfa": opt_hfa}, f)
    else:
        logger.error(f"Optimization failed: {res.message}")

if __name__ == "__main__":
    main()
