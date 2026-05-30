import time
import os
import json
import redis
import requests
import random

def fetch_market_lines():
    """
    Simulated API fetch from a sharp bookmaker.
    In production, replace with: requests.get(API_URL, headers=HEADERS)
    """
    # Mocking a response structure
    events = [
        {"id": "GAME-001", "team_a": "Lakers", "team_b": "Celtics", "market": "Moneyline"},
        {"id": "GAME-002", "team_a": "Warriors", "team_b": "Nets", "market": "Moneyline"},
        {"id": "GAME-003", "team_a": "Heat", "team_b": "Knicks", "market": "Moneyline"}
    ]
    
    signals = []
    for event in events:
        # Simulate varying odds and true probability
        odds = round(random.uniform(1.8, 2.5), 2)
        true_prob = round(random.uniform(0.45, 0.60), 2)
        
        signals.append({
            "market_id": f"{event['id']}-{event['team_a']}",
            "true_prob": true_prob,
            "odds": odds,
            "metadata": {
                "matchup": f"{event['team_a']} vs {event['team_b']}",
                "market": event["market"]
            }
        })
    return signals

def main():
    print("Oracle Agent starting...")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    polling_interval = int(os.getenv("POLLING_INTERVAL", 10))
    
    print(f"Oracle Agent: Polling every {polling_interval}s")
    
    while True:
        try:
            print("Oracle Agent: Fetching latest lines...")
            signals = fetch_market_lines()
            
            for signal in signals:
                r.rpush("market_signals", json.dumps(signal))
                print(f"Oracle Agent: Pushed signal for {signal['market_id']}")
                
        except Exception as e:
            print(f"Oracle Agent Error: {e}")
            
        time.sleep(polling_interval)

if __name__ == "__main__":
    main()
