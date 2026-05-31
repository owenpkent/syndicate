import os
import psycopg2
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SettlementAgent")

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        database="market_history",
        user="syndicate_admin",
        password="changeme_in_env"
    )

def settle_trades():
    """
    Matches trade_history (SUCCESS/ARBITRAGE_LEG) against historical_results
    to determine final PnL.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Find trades that are 'SUCCESS' or 'ARBITRAGE_LEG' and not yet finalized
            # For this simplified logic, we check for games that exist in historical_results
            cur.execute("""
                SELECT th.id, th.market_id, th.executed_odds, hr.home_team, hr.away_team, hr.home_score, hr.away_score
                FROM trade_history th
                JOIN historical_results hr ON th.market_id LIKE '%' || hr.event_id || '%'
                WHERE th.status IN ('SUCCESS', 'ARBITRAGE_LEG')
                AND hr.home_score IS NOT NULL
            """)
            pending = cur.fetchall()
            
            if not pending:
                logger.info("No new trades to settle.")
                return

            for t_id, m_id, odds, h_team, a_team, h_score, a_score in pending:
                # Determine outcome
                # We need to know if the bet was for Home or Away
                # m_id format: "SOURCE-EVENTID-TEAM"
                bet_team = m_id.split("-")[-1]
                
                is_home = (bet_team == h_team)
                win = False
                if is_home and h_score > a_score:
                    win = True
                elif not is_home and a_score > h_score:
                    win = True
                
                new_status = "WIN" if win else "LOSS"
                logger.info(f"Settling Trade {t_id} ({m_id}): {new_status} (Score: {h_score}-{a_score})")
                
                # Update status
                cur.execute("UPDATE trade_history SET status = %s WHERE id = %s", (new_status, t_id))
            
            conn.commit()
            logger.info(f"Successfully settled {len(pending)} trades.")

    except Exception as e:
        logger.error(f"Settlement Error: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    logger.info("Settlement Agent (The Accountant) starting...")
    interval = int(os.getenv("SETTLEMENT_INTERVAL", 60))
    
    while True:
        try:
            settle_trades()
        except Exception as e:
            logger.error(f"Loop Error: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    main()
