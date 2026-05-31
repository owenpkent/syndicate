import psycopg2
import os
import time
from datetime import datetime

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="syndicate_admin",
        password="changeme_in_env"
    )

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def fetch_stats():
    conn = get_db_connection()
    stats = {}
    try:
        with conn.cursor() as cur:
            # Total Trades
            cur.execute("SELECT COUNT(*) FROM trade_history")
            stats['total_trades'] = cur.fetchone()[0]
            
            # Success vs Failed (Slippage)
            cur.execute("SELECT status, COUNT(*) FROM trade_history GROUP BY status")
            stats['status_counts'] = dict(cur.fetchall())
            
            # Arbitrage Margin Tracking
            cur.execute("SELECT COUNT(*) FROM trade_history WHERE status = 'ARBITRAGE_LEG'")
            stats['arb_count'] = cur.fetchone()[0] // 2 # 2 legs per arb
            
            # Avg Fraction (Risk)
            cur.execute("SELECT AVG(fraction) FROM trade_history WHERE status = 'SUCCESS'")
            stats['avg_risk'] = cur.fetchone()[0] or 0
            
            # Latest 10 Trades
            cur.execute("SELECT market_id, executed_odds, fraction, status, executed_timestamp FROM trade_history ORDER BY executed_timestamp DESC LIMIT 10")
            stats['latest_trades'] = cur.fetchall()
            
            # Model Accuracy (Virtual backtest on historical_results)
            cur.execute("SELECT home_team, away_team, home_score, away_score, home_odds, away_odds FROM historical_results")
            hist = cur.fetchall()
            correct = 0
            total_with_odds = 0
            total_games = len(hist)
            for h_team, a_team, h_score, a_score, h_odds, a_odds in hist:
                if h_odds > 0 and a_odds > 0:
                    market_pred_home = 1 if h_odds < a_odds else 0
                    actual_home = 1 if h_score > a_score else 0
                    if market_pred_home == actual_home:
                        correct += 1
                    total_with_odds += 1
            stats['market_accuracy'] = (correct/total_with_odds * 100) if total_with_odds > 0 else 0
            stats['hist_count'] = total_games
            
    finally:
        conn.close()
    return stats

def display_dashboard(stats):
    clear_screen()
    print("="*60)
    print(f" SYNDICATE PERFORMANCE DASHBOARD | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    print(f"\n[SUMMARY]")
    print(f"  Live Signals Processed:  {stats['total_trades']}")
    print(f"  Execution Success Rate:  {(stats['status_counts'].get('SUCCESS', 0) / stats['total_trades'] * 100 if stats['total_trades'] > 0 else 0):.2f}%")
    print(f"  Arbitrage Opps Locked:   {stats['arb_count']}")
    print(f"  Average Risk per Trade:  {float(stats['avg_risk']):.4f} units")
    
    print(f"\n[MODEL PERFORMANCE]")
    print(f"  Historical Games in DB:  {stats['hist_count']}")
    print(f"  Market Closing Accuracy: {stats['market_accuracy']:.2f}%")
    
    print(f"\n[LATEST EXECUTIONS]")
    print(f"{'Market ID':<20} | {'Odds':<8} | {'Size':<8} | {'Status':<10}")
    print("-" * 60)
    for trade in stats['latest_trades']:
        m_id, odds, size, status, ts = trade
        print(f"{m_id:<20} | {float(odds):<8.3f} | {float(size):<8.4f} | {status:<10}")
    
    print("\n" + "="*60)
    print(" (Updating every 5 seconds... Ctrl+C to exit)")

def main():
    while True:
        try:
            stats = fetch_stats()
            display_dashboard(stats)
        except Exception as e:
            print(f"Dashboard Error: {e}")
        time.sleep(5)

if __name__ == "__main__":
    main()
