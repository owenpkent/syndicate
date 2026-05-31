import os
import json
import psycopg2
from nba_api.stats.endpoints import leaguedashteamstats
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("StatsFetcher")

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def fetch_and_store_stats():
    print("Fetching NBA Advanced Team Stats...")
    try:
        # Fetch stats from NBA API
        stats = leaguedashteamstats.LeagueDashTeamStats(measure_type_detailed_defense='Advanced')
        df = stats.get_data_frames()[0]
        
        # Columns: TEAM_ID, TEAM_NAME, GP, W, L, W_PCT, MIN, OFF_RATING, DEF_RATING, NET_RATING, AST_PCT, AST_TO, AST_RATIO, OREB_PCT, DREB_PCT, REB_PCT, TM_TOV_PCT, EFG_PCT, TS_PCT, PACE, PIE, POSS, ...
        
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Create table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS team_advanced_stats (
                    team_name TEXT PRIMARY KEY,
                    off_rating NUMERIC(10, 2),
                    def_rating NUMERIC(10, 2),
                    net_rating NUMERIC(10, 2),
                    pace NUMERIC(10, 2),
                    ts_pct NUMERIC(10, 4),
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Prepare data for insertion
            records = []
            for _, row in df.iterrows():
                records.append((
                    row['TEAM_NAME'],
                    row['OFF_RATING'],
                    row['DEF_RATING'],
                    row['NET_RATING'],
                    row['PACE'],
                    row['TS_PCT']
                ))
            
            # UPSERT into database
            cur.executemany("""
                INSERT INTO team_advanced_stats (team_name, off_rating, def_rating, net_rating, pace, ts_pct)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (team_name) DO UPDATE SET
                off_rating = EXCLUDED.off_rating,
                def_rating = EXCLUDED.def_rating,
                net_rating = EXCLUDED.net_rating,
                pace = EXCLUDED.pace,
                ts_pct = EXCLUDED.ts_pct,
                last_updated = CURRENT_TIMESTAMP
            """, records)
            
            conn.commit()
            print(f"Successfully stored stats for {len(records)} NBA teams.")
            
    except Exception as e:
        logger.error(f"Failed to fetch NBA stats: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    fetch_and_store_stats()
