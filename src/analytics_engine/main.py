import time
import os
import json
import redis
import psycopg2
import pickle
from math_utils import calculate_ev, calculate_kelly_fraction
from advanced_models import LogisticWinProbability, MonteCarloPricer
from portfolio_manager import PortfolioRiskManager
from arbitrage_engine import ArbitrageEngine

def load_settings():
    try:
        with open("/app/config/settings.json", "r") as f:
            return json.load(f)
    except:
        return {
            "safety_buffer_ev": 0.02,
            "kelly_multiplier": 0.25,
            "max_global_exposure_pct": 0.15,
            "correlation_penalty_multiplier": 0.5
        }

def get_db_connection():
    try:
        return psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            database="market_history",
            user="sportsball_admin",
            password="changeme_in_env"
        )
    except Exception as e:
        print(f"Warning: Could not connect to DB: {e}")
        return None

def load_trained_model():
    try:
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

def get_active_trades(r):
    trades_raw = r.hgetall("active_trades")
    return [{"market_id": k, "size": float(v)} for k, v in trades_raw.items()]

def get_team_stats(conn, team_name):
    """
    Fetches the latest advanced stats for a team from PostgreSQL.
    """
    if not conn: return None
    try:
        with conn.cursor() as cur:
            # Flexible matching for team names
            cur.execute("SELECT net_rating, pace FROM team_advanced_stats WHERE team_name ILIKE %s LIMIT 1", (f"%{team_name}%",))
            return cur.fetchone()
    except:
        return None

def main():
    print("Analytics Engine starting...")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    conn = get_db_connection()
    if conn:
        print("Analytics Engine: Connected to PostgreSQL")

    model, ratings = load_trained_model()
    settings = load_settings()
    buffer = settings.get("safety_buffer_ev", 0.02)
    multiplier = settings.get("kelly_multiplier", 0.25)
    
    # Initialize Risk Manager
    risk_manager = PortfolioRiskManager(settings)
    # Initialize Arbitrage Engine
    arb_engine = ArbitrageEngine()

    print(f"Analytics Engine: Monitoring 'market_signals' stream (EV Buffer: {buffer})")
    
    while True:
        signal = r.lpop("market_signals")
        
        if signal:
            try:
                data = json.loads(signal)
                odds = data.get("odds")
                market_id = data.get("market_id", "unknown")

                if "raw_stats" in data:
                    from advanced_models import LogisticWinProbability
                    static_model = LogisticWinProbability()
                    static_model.train([[1500, 1400], [1400, 1500], [1600, 1500]], [1, 0, 1])
                    true_prob = static_model.predict_prob(data["raw_stats"])
                elif model and "metadata" in data and "matchup" in data["metadata"]:
                    try:
                        matchup = data["metadata"]["matchup"]
                        away_team, home_team = matchup.split(" @ ")
                        
                        # 1. Base Elo Ratings
                        r_home = ratings.get(home_team, 1500)
                        r_away = ratings.get(away_team, 1500)
                        
                        # 2. Advanced Stats Enrichment
                        s_home = get_team_stats(conn, home_team)
                        s_away = get_team_stats(conn, away_team)
                        
                        adj_diff = (r_home + 50) - r_away
                        if s_home and s_away:
                            net_diff = float(s_home[0]) - float(s_away[0])
                            adj_diff += (net_diff * 20) # 20 Elo points per 1.0 Net Rating
                        
                        # Get probability from model
                        true_prob = model.predict_proba([[adj_diff]])[0][1]
                        
                        if "participant" in data["metadata"] and data["metadata"]["participant"] != home_team:
                            true_prob = 1 - true_prob
                            
                        print(f"Analytics Engine: Model prediction for {data['metadata']['participant']}: {true_prob:.4f} (Net Rating Adj: {('Yes' if s_home else 'No')})")
                    except Exception as e:
                        print(f"Error predicting from matchup: {e}")
                        true_prob = data.get("true_prob")
                else:
                    true_prob = data.get("true_prob")

                ev = calculate_ev(true_prob, odds)

                # Arbitrage Detection
                if "metadata" in data and "participant" in data["metadata"] and "matchup" in data["metadata"]:
                    try:
                        matchup = data["metadata"]["matchup"]
                        away_team, home_team = matchup.split(" @ ")
                        pt_type = "Home" if data["metadata"]["participant"] == home_team else "Away"
                        source = data["metadata"].get("source", "Unknown")
                        eid = arb_engine.update_odds(market_id, odds, source, pt_type)
                        if eid:
                            arb_opp = arb_engine.check_arbitrage(eid)
                            if arb_opp:
                                print(f"[ARBITRAGE] Found {arb_opp['profit_margin']*100:.2f}% Margin for {eid}!")
                                r.rpush("execution_signals", json.dumps({
                                    "type": "ARBITRAGE",
                                    "event_id": eid,
                                    "margin": arb_opp["profit_margin"],
                                    "legs": arb_opp["legs"]
                                }))
                    except: pass

                if conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO market_history (market_id, odds, true_prob, ev) VALUES (%s, %s, %s, %s)",
                                (market_id, float(odds), float(true_prob), float(ev))
                            )
                        conn.commit()
                    except:
                        conn = get_db_connection()
                
                if ev > buffer:
                    fraction = calculate_kelly_fraction(ev, odds, multiplier)
                    active_trades = get_active_trades(r)
                    adjusted_fraction = risk_manager.evaluate_risk({
                        "market_id": market_id,
                        "fraction": fraction
                    }, active_trades)
                    
                    if adjusted_fraction > 0:
                        print(f"[SIGNAL] Market: {market_id} | EV: {ev:.4f} | Size: {adjusted_fraction:.4f}")
                        r.rpush("execution_signals", json.dumps({
                            "market_id": market_id,
                            "ev": ev,
                            "fraction": adjusted_fraction,
                            "odds": odds
                        }))
                    else:
                        print(f"[RISK REJECT] Market: {market_id} | EV: {ev:.4f} (Portfolio constraints)")
                else:
                    print(f"[REJECT] Market: {market_id} | EV: {ev:.4f} (Below buffer)")
                    
            except Exception as e:
                print(f"Error processing signal: {e}")
        
        time.sleep(1)

if __name__ == "__main__":
    main()
