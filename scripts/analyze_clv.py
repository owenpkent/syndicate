import psycopg2
import os
import numpy as np

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def analyze_clv():
    """
    Analyzes Closing Line Value (CLV).
    Compares the odds we received at execution vs. the final closing odds in the DB.
    """
    print("--- Closing Line Value (CLV) Analysis ---")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Join trade_history (our executed price) with historical_results (the closing price)
            cur.execute("""
                SELECT th.market_id, th.executed_odds, hr.home_odds, hr.away_odds, hr.home_team, hr.away_team
                FROM trade_history th
                JOIN historical_results hr ON th.market_id LIKE '%' || hr.event_id || '%'
                WHERE hr.home_odds > 0 AND hr.away_odds > 0
            """)
            data = cur.fetchall()
            
            if not data:
                print("No matched trades with closing odds found. Run 'make demo' first.")
                return

            clv_improvements = []
            
            for m_id, exec_odds, h_odds, a_odds, h_team, a_team in data:
                # Determine which side we bet on
                bet_team = m_id.split("-")[-1]
                closing_odds = float(h_odds) if bet_team == h_team else float(a_odds)
                
                # CLV = (Executed Odds / Closing Odds) - 1
                if closing_odds > 0:
                    improvement = (float(exec_odds) / closing_odds) - 1
                    clv_improvements.append(improvement)

            avg_clv = np.mean(clv_improvements)
            beat_market_pct = np.mean(np.array(clv_improvements) > 0) * 100

            print(f"Total Trades Analyzed: {len(clv_improvements)}")
            print(f"Average CLV Edge:     {avg_clv*100:.2f}%")
            print(f"Beat Closing Line:    {beat_market_pct:.2f}% of the time")
            
            if avg_clv > 0.02:
                print("\nSTATUS: STRATEGY IS ALPHA-POSITIVE (Beating market by >2%)")
            elif avg_clv > 0:
                print("\nSTATUS: STRATEGY IS MARGINAL (Beating market slightly)")
            else:
                print("\nSTATUS: STRATEGY IS SUB-PAR (Model is lagging behind market movement)")

    finally:
        conn.close()

if __name__ == "__main__":
    analyze_clv()
