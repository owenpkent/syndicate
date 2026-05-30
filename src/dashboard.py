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
            
            # Avg Fraction (Risk)
            cur.execute("SELECT AVG(fraction) FROM trade_history WHERE status = 'SUCCESS'")
            stats['avg_risk'] = cur.fetchone()[0] or 0
            
            # Latest 10 Trades
            cur.execute("SELECT market_id, executed_odds, fraction, status, executed_timestamp FROM trade_history ORDER BY executed_timestamp DESC LIMIT 10")
            stats['latest_trades'] = cur.fetchall()
            
    finally:
        conn.close()
    return stats

def display_dashboard(stats):
    clear_screen()
    print("="*60)
    print(f" SYNDICATE PERFORMANCE DASHBOARD | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    print(f"\n[SUMMARY]")
    print(f"  Total Signals Processed: {stats['total_trades']}")
    print(f"  Execution Success Rate:  {(stats['status_counts'].get('SUCCESS', 0) / stats['total_trades'] * 100 if stats['total_trades'] > 0 else 0):.2f}%")
    print(f"  Average Risk per Trade:  {float(stats['avg_risk']):.4f} units")
    
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
