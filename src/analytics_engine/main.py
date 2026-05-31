import time
import os
import json
import redis
import psycopg2
from math_utils import calculate_ev, calculate_kelly_fraction
from advanced_models import LogisticWinProbability, MonteCarloPricer

# Example of Advanced Model Initialization
# In production, this would load a pre-trained model file from /app/models
win_prob_model = LogisticWinProbability()
# Dummy training to illustrate functionality; in production, this would be a loaded state
win_prob_model.train([[1500, 1400], [1400, 1500], [1600, 1500]], [1, 0, 1])

def load_settings():
    # In a real container, we'd mount this or use env vars
    # For now, we'll use defaults if the file isn't reachable
    try:
        with open("/app/config/settings.json", "r") as f:
            return json.load(f)
    except:
        return {
            "safety_buffer_ev": 0.02,
            "kelly_multiplier": 0.25
        }

def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            database="market_history",
            user="syndicate_admin",
            password="changeme_in_env"
        )
    except Exception as e:
        print(f"Warning: Could not connect to DB: {e}")
        return None

import pickle

def load_trained_model():
    try:
        # Check if model files exist
        if os.path.exists("models/win_prob_model.pkl") and os.path.exists("models/current_ratings.json"):
            with open("models/win_prob_model.pkl", "rb") as f:
                model = pickle.load(f)
            with open("models/current_ratings.json", "r") as f:
                ratings = json.load(f)
            print("Analytics Engine: Loaded trained model and ratings.")
            return model, ratings
    except Exception as e:
        print(f"Analytics Engine: Warning - Could not load trained model: {e}")
    return None, {}

def main():
    print("Analytics Engine starting...")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    # Initialize DB connection
    conn = get_db_connection()
    if conn:
        print("Analytics Engine: Connected to PostgreSQL")

    # Load trained artifacts
    model, ratings = load_trained_model()

    settings = load_settings()
    buffer = settings.get("safety_buffer_ev", 0.02)
    multiplier = settings.get("kelly_multiplier", 0.25)

    print(f"Analytics Engine: Monitoring 'market_signals' stream (EV Buffer: {buffer})")
    
    while True:
        # Pull from Redis (Simulating receiving a signal from Oracle/Scout)
        # In a real app, use r.xread or r.blpop
        signal = r.lpop("market_signals")
        
        if signal:
            try:
                data = json.loads(signal)
                odds = data.get("odds")
                market_id = data.get("market_id", "unknown")

                # ADVANCED QUANT PATH:
                if "raw_stats" in data:
                    true_prob = win_prob_model.predict_prob(data["raw_stats"])
                elif model and "metadata" in data and "matchup" in data["metadata"]:
                    # Matchup-based prediction using current Elo ratings
                    try:
                        matchup = data["metadata"]["matchup"]
                        # Matchup format: "Away @ Home"
                        away_team, home_team = matchup.split(" @ ")
                        r_home = ratings.get(home_team, 1500)
                        r_away = ratings.get(away_team, 1500)
                        
                        # Optimization params (fallback if not in model intercept)
                        diff = (r_home + 50) - r_away # Assuming default HFA=50 for now
                        
                        # Get probability from model
                        true_prob = model.predict_proba([[diff]])[0][1]
                        
                        # If the participant isn't the home team, we need to adjust
                        # This logic assumes the signal is for a specific participant
                        if "participant" in data["metadata"]:
                            if data["metadata"]["participant"] != home_team:
                                true_prob = 1 - true_prob
                                
                        print(f"Analytics Engine: Model prediction for {data['metadata']['participant']}: {true_prob:.4f}")
                    except Exception as e:
                        print(f"Error predicting from matchup: {e}")
                        true_prob = data.get("true_prob")
                else:
                    true_prob = data.get("true_prob")

                ev = calculate_ev(true_prob, odds)

                # Persist to DB
                if conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO market_history (market_id, odds, true_prob, ev) VALUES (%s, %s, %s, %s)",
                                (market_id, float(odds), float(true_prob), float(ev))
                            )
                        conn.commit()
                    except Exception as e:
                        print(f"DB Error: {e}")
                        conn = get_db_connection() # Attempt reconnect next time
                
                if ev > buffer:
                    fraction = calculate_kelly_fraction(ev, odds, multiplier)
                    print(f"[SIGNAL] Market: {market_id} | EV: {ev:.4f} | Kelly: {fraction:.4f}")
                    
                    # Pass to Sniper Agent
                    r.rpush("execution_signals", json.dumps({
                        "market_id": market_id,
                        "ev": ev,
                        "fraction": fraction,
                        "odds": odds
                    }))
                else:
                    print(f"[REJECT] Market: {market_id} | EV: {ev:.4f} (Below buffer)")
                    
            except Exception as e:
                print(f"Error processing signal: {e}")
        
        time.sleep(1)

if __name__ == "__main__":
    main()
