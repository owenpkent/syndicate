import os
import json
import psycopg2
import numpy as np
from sklearn.linear_model import LogisticRegression
import pickle
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ModelTrainer")

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        database=os.getenv("POSTGRES_DB", "market_history"),
        user=os.getenv("POSTGRES_USER", "sportsball_admin"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme_in_env"),
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

def generate_features(results, k_factor, hfa):
    ratings = {}
    features = []
    labels = []
    
    for _, home_team, away_team, home_score, away_score in results:
        r_home = ratings.get(home_team, 1500)
        r_away = ratings.get(away_team, 1500)
        
        # Feature: Rating Differential + HFA
        diff = (r_home + hfa) - r_away
        features.append([diff])
        
        # Outcome
        actual = 1 if home_score > away_score else 0
        labels.append(actual)
        
        # Update Elo for next game
        exp_home = 1 / (1 + 10 ** ((r_away - (r_home + hfa)) / 400))
        shift = k_factor * (actual - exp_home)
        ratings[home_team] = r_home + shift
        ratings[away_team] = r_away - shift
        
    return np.array(features), np.array(labels), ratings

def main():
    # 1. Load optimized params
    try:
        with open("optimized_params.json", "r") as f:
            params = json.load(f)
            k_factor = params["k_factor"]
            hfa = params["hfa"]
    except FileNotFoundError:
        logger.error("optimized_params.json not found. Run rating_optimizer.py first.")
        return

    # 2. Fetch data
    results = fetch_historical_data()
    if not results:
        logger.error("No historical data found.")
        return

    # 3. Generate Features
    logger.info("Generating features from Elo simulation...")
    X, y, final_ratings = generate_features(results, k_factor, hfa)

    # 4. Train Model
    logger.info(f"Training Logistic Regression on {len(X)} samples...")
    model = LogisticRegression()
    model.fit(X, y)
    
    # Calculate accuracy
    accuracy = model.score(X, y)
    logger.info(f"Model Training Complete. Accuracy: {accuracy:.4f}")

    # 5. Save Model and Final Ratings
    os.makedirs("models", exist_ok=True)
    with open("models/win_prob_model.pkl", "wb") as f:
        pickle.dump(model, f)
        
    with open("models/current_ratings.json", "w") as f:
        json.dump(final_ratings, f)
        
    logger.info("Model and current ratings saved to /app/models/")

if __name__ == "__main__":
    main()
