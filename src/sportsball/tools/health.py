"""System health check — real probes, not hardcoded ``[UP]``.

Checks Redis reachability, queue depths, live exposure, and PostgreSQL
reachability + table row counts. Exits non-zero if a core dependency is down so
it can be used in CI / monitoring.
"""
from __future__ import annotations

import sys

from ..broker import Broker, EXECUTION_SIGNALS, MARKET_SIGNALS
from ..config import load_settings
from ..db import Database


def check(settings) -> bool:
    print("--- Sportsball System Health ---")
    healthy = True

    broker = Broker(settings.redis)
    if broker.ping():
        print("[OK]   Redis broker reachable")
        print(f"[INFO] market_signals queued:    {broker.queue_depth(MARKET_SIGNALS)}")
        print(f"[INFO] execution_signals queued: {broker.queue_depth(EXECUTION_SIGNALS)}")
        print(f"[INFO] active exposures:         {len(broker.active_trades())} "
              f"({broker.total_exposure():.4f} units)")
    else:
        print("[FAIL] Redis broker unreachable")
        healthy = False

    db = Database(settings.db)
    if db.available:
        print("[OK]   PostgreSQL reachable")
        for table in ("market_history", "trade_history", "historical_results", "team_advanced_stats"):
            try:
                count = db.query_one(f"SELECT COUNT(*) FROM {table}")[0]
                print(f"[INFO] {table:<22} {count} rows")
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] {table}: {exc}")
    else:
        print("[FAIL] PostgreSQL unreachable")
        healthy = False

    print(f"\nSYSTEM HEALTH: {'[OK]' if healthy else '[DEGRADED]'}")
    return healthy


def main() -> None:
    sys.exit(0 if check(load_settings()) else 1)


if __name__ == "__main__":
    main()
