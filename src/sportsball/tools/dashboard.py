"""Real-time CLI performance dashboard (reads the normalized schema)."""
from __future__ import annotations

import os
import time
from datetime import datetime

from ..config import load_settings
from ..db import Database


def fetch_stats(db: Database) -> dict:
    stats: dict = {}
    stats["total_trades"] = db.query_one("SELECT COUNT(*) FROM trades")[0]
    stats["status_counts"] = dict(db.query("SELECT status, COUNT(*) FROM trades GROUP BY status"))
    stats["arb_count"] = db.query_one("SELECT COUNT(*) FROM trades WHERE is_arb")[0] // 2
    stats["avg_risk"] = db.query_one(
        "SELECT AVG(stake_frac) FROM trades WHERE status IN ('OPEN','WIN','LOSS')")[0] or 0
    stats["realized_pnl"] = db.query_one(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE pnl IS NOT NULL")[0] or 0
    stats["latest"] = db.query(
        "SELECT market_id, side, executed_odds, stake_frac, status FROM trades "
        "ORDER BY executed_ts DESC LIMIT 10")

    # Market baseline: how often the closing favorite actually won.
    row = db.query_one(
        """
        SELECT COUNT(*) FILTER (
                 WHERE (home_close < away_close) = (home_score > away_score)),
               COUNT(*)
        FROM events
        WHERE status = 'FINAL' AND home_close > 0 AND away_close > 0
        """)
    correct, graded = (row or (0, 0))
    stats["favorite_hit_rate"] = (correct / graded * 100) if graded else 0
    stats["event_count"] = db.query_one("SELECT COUNT(*) FROM events")[0]
    return stats


def render(stats: dict) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    line = "=" * 64
    print(line)
    print(f" SPORTSBALL DASHBOARD | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(line)
    total = stats["total_trades"]
    settled = stats["status_counts"].get("WIN", 0) + stats["status_counts"].get("LOSS", 0)
    print("\n[SUMMARY]")
    print(f"  Trades recorded:        {total}")
    print(f"  Settled (WIN+LOSS):     {settled}")
    print(f"  Realized PnL:           {float(stats['realized_pnl']):+.4f} units")
    print(f"  Arbitrage opps locked:  {stats['arb_count']}")
    print(f"  Avg stake per trade:    {float(stats['avg_risk']):.4f} units")
    print("\n[MARKET BASELINE]")
    print(f"  Events in DB:           {stats['event_count']}")
    print(f"  Favorite hit rate:      {stats['favorite_hit_rate']:.2f}%  (the bar the model must beat)")
    print("\n[LATEST EXECUTIONS]")
    print(f"{'Market ID':<26} | {'Side':<4} | {'Odds':<7} | {'Size':<7} | {'Status':<8}")
    print("-" * 64)
    for market_id, side, odds, size, status in stats["latest"]:
        print(f"{str(market_id):<26} | {side:<4} | {float(odds):<7.3f} | {float(size):<7.4f} | {status:<8}")
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
