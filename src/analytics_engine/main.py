import time
import os
import json
import redis
import psycopg2
from math_utils import calculate_ev, calculate_kelly_fraction

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

def main():
    print("Analytics Engine starting...")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    # Initialize DB connection
    conn = get_db_connection()
    if conn:
        print("Analytics Engine: Connected to PostgreSQL")

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
                true_prob = data.get("true_prob")
                odds = data.get("odds")
                market_id = data.get("market_id", "unknown")

                ev = calculate_ev(true_prob, odds)

                # Persist to DB
                if conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO market_history (market_id, odds, true_prob, ev) VALUES (%s, %s, %s, %s)",
                                (market_id, odds, true_prob, ev)
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
