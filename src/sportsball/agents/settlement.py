"""Settlement Agent — the accountant.

Matches open paper trades against final results, marks them WIN/LOSS, and — new
in this rewrite — clears the settled position's exposure from the
``active_trades`` hash. Without this reaper the global-exposure guard eventually
rejected every new bet because exposure only ever grew.
"""
from __future__ import annotations

import time

from ..broker import Broker
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger

log = get_logger("settlement")

# trade statuses that represent an open, unsettled position
OPEN_STATUSES = ("SUCCESS", "ARBITRAGE_LEG")

PENDING_QUERY = """
    SELECT th.id, th.market_id, hr.home_team, hr.away_team, hr.home_score, hr.away_score
    FROM trade_history th
    JOIN historical_results hr ON th.market_id LIKE '%%' || hr.event_id || '%%'
    WHERE th.status IN %s AND hr.home_score IS NOT NULL
"""


def settle_once(db: Database, broker: Broker) -> int:
    """Settle all matchable open trades; returns the count settled."""
    if not db.available:
        return 0
    rows = db.query(PENDING_QUERY, (OPEN_STATUSES,))
    if not rows:
        log.info("No new trades to settle.")
        return 0

    for trade_id, market_id, home_team, _away, home_score, away_score in rows:
        bet_team = market_id.split("-")[-1]
        is_home = bet_team == home_team
        won = (is_home and home_score > away_score) or (not is_home and away_score > home_score)
        status = "WIN" if won else "LOSS"
        db.execute("UPDATE trade_history SET status = %s WHERE id = %s", (status, trade_id))
        broker.clear_exposure(market_id)  # reaper: free the exposure slot
        log.info("Settled %s (%s): %s (%s-%s)", trade_id, market_id, status, home_score, away_score)

    log.info("Settled %d trades.", len(rows))
    return len(rows)


def run(settings: Settings) -> None:
    db = Database(settings.db)
    broker = Broker(settings.redis)
    interval = settings.settlement_interval
    while True:
        try:
            settle_once(db, broker)
        except Exception as exc:  # noqa: BLE001
            log.error("Settlement loop error: %s", exc)
        time.sleep(interval)


def main() -> None:
    log.info("Settlement Agent (the accountant) starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
