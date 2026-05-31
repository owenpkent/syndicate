"""Settlement Agent — the accountant.

Matches OPEN trades against FINAL events via a foreign-key join (no more
``LIKE '%' || event_id || '%'`` matching), grades them WIN/LOSS, writes the
realized PnL (in stake-fraction units), and clears the settled position's
exposure from the ``active_trades`` hash so the global-exposure guard doesn't
ratchet shut.
"""
from __future__ import annotations

import time

from ..broker import Broker
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..store import HOME, PendingTrade, Store

log = get_logger("settlement")


def grade(trade: PendingTrade) -> tuple[str, float]:
    """Return (status, pnl) for a settled trade. PnL is in stake-fraction units."""
    won = ((trade.side == HOME and trade.home_score > trade.away_score)
           or (trade.side != HOME and trade.away_score > trade.home_score))
    if won:
        return "WIN", float(trade.stake_frac) * (float(trade.executed_odds) - 1)
    return "LOSS", -float(trade.stake_frac)


def settle_once(store: Store, broker: Broker) -> int:
    """Settle all matchable open trades; returns the count settled."""
    if not store.available:
        return 0
    pending = store.pending_settlements()
    if not pending:
        log.info("No new trades to settle.")
        return 0

    for trade in pending:
        status, pnl = grade(trade)
        store.settle_trade(trade.trade_id, status, pnl)
        if trade.market_id:
            broker.clear_exposure(trade.market_id)  # reaper: free the exposure slot
        log.info("Settled %s (%s): %s pnl=%.4f (%s-%s)", trade.trade_id, trade.side,
                 status, pnl, trade.home_score, trade.away_score)

    log.info("Settled %d trades.", len(pending))
    return len(pending)


def run(settings: Settings) -> None:
    store = Store(Database(settings.db))
    broker = Broker(settings.redis)
    interval = settings.settlement_interval
    while True:
        try:
            settle_once(store, broker)
        except Exception as exc:  # noqa: BLE001
            log.error("Settlement loop error: %s", exc)
        time.sleep(interval)


def main() -> None:
    log.info("Settlement Agent (the accountant) starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
