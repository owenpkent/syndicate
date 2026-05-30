import time
import os
import json
import redis
import random

def simulate_execution(market_id, odds, fraction, slippage_tolerance):
    """
    Simulates a paper trade execution with slippage.
    """
    # Simulate execution slippage (0.1% to 0.5%)
    actual_slippage = random.uniform(0.001, 0.005)
    executed_odds = odds * (1 - actual_slippage)
    
    if actual_slippage > slippage_tolerance:
        return {
            "status": "FAILED",
            "reason": f"Slippage ({actual_slippage:.4f}) exceeded tolerance ({slippage_tolerance})"
        }
    
    return {
        "status": "SUCCESS",
        "executed_odds": round(executed_odds, 4),
        "fraction": round(fraction, 4),
        "timestamp": time.time()
    }

import psycopg2

def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST", "postgres"),
            database="market_history",
            user="syndicate_admin",
            password="changeme_in_env"
        )
    except Exception as e:
        print(f"Warning: Could not connect to DB: {e}")
        return None

def main():
    print("Sniper Agent starting...")
    mode = os.getenv("EXECUTION_MODE", "PAPER")
    slippage_tolerance = float(os.getenv("SLIPPAGE_TOLERANCE_PCT", 0.005))
    
    print(f"Sniper Agent: Mode={mode} | Slippage Tolerance={slippage_tolerance}")
    
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    # Initialize DB connection
    conn = get_db_connection()
    if conn:
        print("Sniper Agent: Connected to PostgreSQL")

    print("Sniper Agent: Monitoring 'execution_signals'...")
    
    while True:
        # Pull from Redis
        signal = r.lpop("execution_signals")
        
        if signal:
            try:
                data = json.loads(signal)
                market_id = data.get("market_id")
                odds = data.get("odds")
                fraction = data.get("fraction")
                
                print(f"[TARGET] Received execution signal for {market_id} (Odds: {odds}, Size: {fraction:.4f})")
                
                status = "SKIPPED"
                final_odds = 0
                
                if mode == "PAPER":
                    result = simulate_execution(market_id, odds, fraction, slippage_tolerance)
                    status = result["status"]
                    
                    if status == "SUCCESS":
                        final_odds = result['executed_odds']
                        print(f"[EXECUTE] SUCCESS | Market: {market_id} | Final Odds: {final_odds} | Size: {result['fraction']}")
                    else:
                        print(f"[EXECUTE] REJECTED | Market: {market_id} | Reason: {result['reason']}")
                else:
                    print(f"[EXECUTE] SKIPPED | Mode is {mode} (Real execution not implemented)")
                
                # Persist to DB
                if conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO trade_history (market_id, executed_odds, fraction, status) VALUES (%s, %s, %s, %s)",
                                (market_id, final_odds, fraction, status)
                            )
                        conn.commit()
                    except Exception as e:
                        print(f"DB Error: {e}")
                        conn = get_db_connection()
                    
            except Exception as e:
                print(f"Sniper Agent Error: {e}")
        
        time.sleep(0.5)

if __name__ == "__main__":
    main()
