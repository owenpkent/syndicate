import time
import os
import json
import redis
import requests
import random
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OracleAgent")

def american_to_decimal(american_odds):
    """
    Converts American odds (e.g., -110, +150) to Decimal odds (e.g., 1.91, 2.50).
    """
    if american_odds == 0:
        return 0
    if american_odds > 0:
        return round((american_odds / 100) + 1, 4)
    else:
        return round((100 / abs(american_odds)) + 1, 4)

def fetch_rundown_markets(api_key):
    """
    Fetches live market data from The Rundown API v2.
    Defaulting to NBA (Sport ID: 4).
    """
    # Get today's date in YYYY-MM-DD format
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    
    url = f"https://therundown.io/api/v2/sports/4/events/{today}"
    headers = {
        "X-TheRundown-Key": api_key
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        signals = []
        for event in data.get("events", []):
            event_id = event.get("event_id")
            teams = event.get("teams", [])
            home_team = next((t["name"] for t in teams if not t.get("is_away")), "Home")
            away_team = next((t["name"] for t in teams if t.get("is_away")), "Away")
            
            # Extract Moneyline (Market ID: 1)
            moneyline_market = next((m for m in event.get("markets", []) if m.get("market_id") == 1), None)
            
            if moneyline_market:
                for participant in moneyline_market.get("participants", []):
                    # Pick a primary sportsbook (Affiliate ID: 19 = Pinnacle as example)
                    # The Rundown response maps prices by Affiliate ID
                    lines = participant.get("lines", [])
                    if lines:
                        prices = lines[0].get("prices", {})
                        # Try to find a valid price (using the first available if 19 isn't there)
                        affiliate_id = "19" 
                        if affiliate_id not in prices:
                            affiliate_id = next(iter(prices.keys())) if prices else None
                        
                        if affiliate_id:
                            american_odds = prices[affiliate_id].get("price")
                            
                            # Standard API behavior: 0.0001 means Off Board
                            if american_odds and american_odds != 0.0001:
                                decimal_odds = american_to_decimal(american_odds)
                                
                                # Simulating a true_prob for the example
                                # In a real system, the Analytics Engine might calculate this from stats
                                # Or the Oracle provides a 'consensus' probability
                                true_prob = round(random.uniform(0.45, 0.60), 2)
                                
                                signals.append({
                                    "market_id": f"RUNDOWN-{event_id}-{participant['name']}",
                                    "true_prob": true_prob,
                                    "odds": decimal_odds,
                                    "metadata": {
                                        "source": "The Rundown",
                                        "matchup": f"{away_team} @ {home_team}",
                                        "participant": participant['name'],
                                        "affiliate_id": affiliate_id
                                    }
                                })
        return signals
    except Exception as e:
        logger.error(f"Failed to fetch from The Rundown: {e}")
        return None

def fetch_mock_lines():
    """
    Fallback mock mode if API is unavailable.
    """
    logger.info("Oracle Agent: Using Mock Mode...")
    events = [
        {"id": "MOCK-001", "team_a": "Lakers", "team_b": "Celtics"},
        {"id": "MOCK-002", "team_b": "Nets", "team_a": "Warriors"}
    ]
    signals = []
    for event in events:
        odds = round(random.uniform(1.8, 2.5), 2)
        true_prob = round(random.uniform(0.45, 0.60), 2)
        signals.append({
            "market_id": f"{event['id']}-{event['team_a']}",
            "true_prob": true_prob,
            "odds": odds,
            "metadata": {"source": "Mock Engine"}
        })
    return signals

def main():
    logger.info("Oracle Agent starting...")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    api_key = os.getenv("RUNDOWN_API_KEY")
    polling_interval = int(os.getenv("POLLING_INTERVAL", 30))
    
    while True:
        signals = None
        if api_key and api_key != "your_rundown_api_key_here":
            logger.info("Oracle Agent: Fetching live lines from The Rundown...")
            signals = fetch_rundown_markets(api_key)
        
        # Fallback to mock if no key or API failed
        if signals is None:
            signals = fetch_mock_lines()
            
        for signal in signals:
            r.rpush("market_signals", json.dumps(signal))
            logger.info(f"Pushed signal for {signal['market_id']}")
            
        time.sleep(polling_interval)

if __name__ == "__main__":
    main()
