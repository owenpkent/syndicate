"""Real-time CLI performance dashboard (reads ``trade_history`` + results)."""
from __future__ import annotations

import os
import time
from datetime import datetime

from ..config import load_settings
from ..db import Database


def fetch_stats(db: Database) -> dict:
    stats: dict = {}
    stats["total_trades"] = db.query_one("SELECT COUNT(*) FROM trade_history")[0]
    stats["status_counts"] = dict(db.query("SELECT status, COUNT(*) FROM trade_history GROUP BY status"))
    stats["arb_count"] = db.query_one(
        "SELECT COUNT(*) FROM trade_history WHERE status = 'ARBITRAGE_LEG'")[0] // 2
    stats["avg_risk"] = db.query_one(
        "SELECT AVG(fraction) FROM trade_history WHERE status = 'SUCCESS'")[0] or 0
    stats["latest_trades"] = db.query(
        "SELECT market_id, executed_odds, fraction, status, executed_timestamp "
        "FROM trade_history ORDER BY executed_timestamp DESC LIMIT 10")

    hist = db.query("SELECT home_score, away_score, home_odds, away_odds FROM historical_results")
    correct = graded = 0
    for hs, as_, ho, ao in hist:
        if ho and ao and ho > 0 and ao > 0:
            graded += 1
            if (1 if ho < ao else 0) == (1 if hs > as_ else 0):
                correct += 1
    stats["favorite_hit_rate"] = (correct / graded * 100) if graded else 0
    stats["hist_count"] = len(hist)
    return stats


def render(stats: dict) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    line = "=" * 60
    print(line)
    print(f" SPORTSBALL DASHBOARD | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(line)
    total = stats["total_trades"]
    success = stats["status_counts"].get("SUCCESS", 0)
    print("\n[SUMMARY]")
    print(f"  Trades recorded:        {total}")
    print(f"  Execution success rate: {(success / total * 100 if total else 0):.2f}%")
    print(f"  Arbitrage opps locked:  {stats['arb_count']}")
    print(f"  Avg risk per trade:     {float(stats['avg_risk']):.4f} units")
    print("\n[MARKET BASELINE]")
    print(f"  Historical games in DB: {stats['hist_count']}")
    print(f"  Favorite hit rate:      {stats['favorite_hit_rate']:.2f}%  (the bar the model must beat)")
    print("\n[LATEST EXECUTIONS]")
    print(f"{'Market ID':<24} | {'Odds':<8} | {'Size':<8} | {'Status':<10}")
    print("-" * 60)
    for m_id, odds, size, status, _ts in stats["latest_trades"]:
        print(f"{m_id:<24} | {float(odds):<8.3f} | {float(size):<8.4f} | {status:<10}")
    print("\n" + line)
    print(" (Updating every 5s... Ctrl+C to exit)")


def main() -> None:
    db = Database(load_settings().db)
    while True:
        try:
            render(fetch_stats(db))
        except Exception as exc:  # noqa: BLE001
            print(f"Dashboard error: {exc}")
        time.sleep(5)


if __name__ == "__main__":
    main()
