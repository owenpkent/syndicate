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
from ..notify import build_notifier


def probe(settings) -> tuple[bool, list[str]]:
    """Run the health checks; return ``(healthy, lines)`` and print each line.

    Lines are accumulated so they can be forwarded to Slack on degradation,
    while the stdout output (and the eventual exit code) is unchanged.
    """
    lines: list[str] = []

    def emit(line: str) -> None:
        print(line)
        lines.append(line)

    emit("--- Sportsball System Health ---")
    healthy = True

    broker = Broker(settings.redis)
    if broker.ping():
        emit("[OK]   Redis broker reachable")
        emit(f"[INFO] market_signals queued:    {broker.queue_depth(MARKET_SIGNALS)}")
        emit(f"[INFO] execution_signals queued: {broker.queue_depth(EXECUTION_SIGNALS)}")
        emit(f"[INFO] active exposures:         {len(broker.active_trades())} "
             f"({broker.total_exposure():.4f} units)")
    else:
        emit("[FAIL] Redis broker unreachable")
        healthy = False

    db = Database(settings.db)
    if db.available:
        emit("[OK]   PostgreSQL reachable")
        for table in ("events", "signals", "trades", "team_advanced_stats"):
            try:
                count = db.query_one(f"SELECT COUNT(*) FROM {table}")[0]
                emit(f"[INFO] {table:<22} {count} rows")
            except Exception as exc:  # noqa: BLE001
                emit(f"[WARN] {table}: {exc}")
    else:
        emit("[FAIL] PostgreSQL unreachable")
        healthy = False

    emit(f"\nSYSTEM HEALTH: {'[OK]' if healthy else '[DEGRADED]'}")
    return healthy, lines


def check(settings) -> bool:
    """Backward-compatible boolean health result."""
    return probe(settings)[0]


def main() -> None:
    healthy, lines = probe(load_settings())
    if not healthy:
        build_notifier(load_settings()).notify_health(False, lines)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
