import os
import requests
import json
import psycopg2
import argparse
from datetime import datetime, timedelta
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HistoricalScraper")

def american_to_decimal(american_odds):
    if american_odds == 0 or american_odds == 0.0001:
        return 0
    if american_odds > 0:
        return round((american_odds / 100) + 1, 4)
    else:
        return round((100 / abs(american_odds)) + 1, 4)

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def scrape_date(date_str, sport_id, api_key):
    url = f"https://therundown.io/api/v2/sports/{sport_id}/events/{date_str}"
    headers = {"X-TheRundown-Key": api_key}
    
    max_retries = 3
    retry_delay = 10
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429:
                logger.warning(f"Rate limited on {date_str}. Retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2
                continue
                
            response.raise_for_status()
            events = response.json().get("events", [])
            
            parsed_records = []
            for event in events:
                # Only collect finished games
                score_data = event.get("score", {})
                if score_data.get("event_status") != "STATUS_FINAL":
                    continue
                    
                event_id = event.get("event_id")
                event_date = event.get("event_date")
                teams = event.get("teams", [])
                
                home_team_data = next((t for t in teams if not t.get("is_away")), {})
                away_team_data = next((t for t in teams if t.get("is_away")), {})
                
                home_team = home_team_data.get("name")
                away_team = away_team_data.get("name")
                home_score = score_data.get("score_home")
                away_score = score_data.get("score_away")
                
                # Extract Moneyline Odds (Market ID: 1)
                moneyline_market = next((m for m in event.get("markets", []) if m.get("market_id") == 1), None)
                home_odds = 0
                away_odds = 0
                
                if moneyline_market:
                    for part in moneyline_market.get("participants", []):
                        lines = part.get("lines", [])
                        if lines:
                            prices = lines[0].get("prices", {})
                            aff_id = "19" if "19" in prices else next(iter(prices.keys())) if prices else None
                            
                            if aff_id:
                                am_odds = prices[aff_id].get("price")
                                dec_odds = american_to_decimal(am_odds)
                                
                                p_name = part.get("name", "").lower()
                                if home_team.lower() in p_name or p_name in home_team.lower():
                                    home_odds = dec_odds
                                elif away_team.lower() in p_name or p_name in away_team.lower():
                                    away_odds = dec_odds
                
                parsed_records.append((
                    event_id, sport_id, event_date, home_team, away_team,
                    home_score, away_score, home_odds, away_odds
                ))
            return parsed_records

        except Exception as e:
            logger.error(f"Error scraping {date_str} (Attempt {attempt+1}): {e}")
            time.sleep(retry_delay)
            retry_delay *= 2
            
    return []

def main():
    parser = argparse.ArgumentParser(description="Sportsball Historical Data Scraper")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--sport-id", type=int, default=4, help="NBA=4, NFL=2")
    
    args = parser.parse_args()
    api_key = os.getenv("RUNDOWN_API_KEY")
    
    if not api_key:
        logger.error("RUNDOWN_API_KEY environment variable not set.")
        return

    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")
    
    conn = get_db_connection()
    
    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        logger.info(f"Scraping {date_str} (Sport ID: {args.sport_id})...")
        
        records = scrape_date(date_str, args.sport_id, api_key)
        
        if records:
            try:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO historical_results 
                        (event_id, sport_id, event_date, home_team, away_team, home_score, away_score, home_odds, away_odds)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_id) DO UPDATE SET
                        home_score = EXCLUDED.home_score,
                        away_score = EXCLUDED.away_score,
                        home_odds = EXCLUDED.home_odds,
                        away_odds = EXCLUDED.away_odds
                    """, records)
                conn.commit()
                logger.info(f"Inserted/Updated {len(records)} records for {date_str}")
            except Exception as e:
                logger.error(f"Database error on {date_str}: {e}")
                conn.rollback()
        
        # Increase sleep to respect rate limits
        time.sleep(10)
        current += timedelta(days=1)
        
    conn.close()
    logger.info("Scraping complete.")

if __name__ == "__main__":
    main()
