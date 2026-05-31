"""Sniper Agent — the executioner.

Drains ``execution_signals`` and, in PAPER mode, simulates a fill with slippage,
records the trade (with the ``event_id``/``side`` the Engine already resolved),
and tracks the position's exposure in ``active_trades`` (later cleared by the
Settlement reaper).
"""
from __future__ import annotations

import random

from ..broker import Broker, EXECUTION_SIGNALS
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..store import Store, parse_market_id

log = get_logger("sniper")

INFLIGHT = "execution_signals:inflight"


def simulate_fill(odds: float, tolerance: float, rng: random.Random) -> dict:
    """Simulate a paper fill with slippage drawn in [0.1%, 0.5%]."""
    slippage = rng.uniform(0.001, 0.005)
    if slippage > tolerance:
        return {"status": "FAILED", "reason": f"slippage {slippage:.4f} > tol {tolerance}"}
    return {"status": "SUCCESS", "executed_odds": round(odds * (1 - slippage), 4)}


def handle_arbitrage(data: dict, store: Store) -> None:
    event_id = data["event_id"]
    log.info("[ARBITRAGE] executing %d legs for %s | margin %.2f%%",
             len(data["legs"]), event_id, data["margin"] * 100)
    for leg in data["legs"]:
        log.info("  leg %s | odds %s | alloc %.4f", leg["market_id"], leg["odds"], leg["allocation"])
        if store.available:
            try:
                _, leg_event, _ = parse_market_id(leg["market_id"])
                store.record_trade(leg_event, leg.get("side", "HOME"), leg.get("source"),
                                   leg["odds"], leg["allocation"], "ARB_LEG",
                                   market_id=leg["market_id"], is_arb=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to persist arb leg: %s", exc)


def handle_value(data: dict, *, mode: str, tolerance: float, store: Store, broker: Broker,
                 rng: random.Random) -> None:
    market_id, odds, fraction = data["market_id"], data["odds"], data["fraction"]
    event_id, side, source = data.get("event_id"), data.get("side", "HOME"), data.get("source")
    log.info("[TARGET] %s | odds %s | size %.4f", market_id, odds, fraction)

    if mode != "PAPER":
        log.info("[SKIP] mode=%s (live execution not implemented)", mode)
        return

    result = simulate_fill(odds, tolerance, rng)
    if result["status"] == "SUCCESS":
        final = result["executed_odds"]
        log.info("[EXECUTE] SUCCESS %s | final odds %s", market_id, final)
        broker.set_exposure(market_id, fraction)
        if store.available and event_id:
            store.record_trade(event_id, side, source, final, fraction, "OPEN", market_id=market_id)
    else:
        log.info("[EXECUTE] REJECTED %s | %s", market_id, result["reason"])
        if store.available and event_id:
            store.record_trade(event_id, side, source, 0, fraction, "FAILED", market_id=market_id)


def run(settings: Settings) -> None:
    broker = Broker(settings.redis)
    store = Store(Database(settings.db))
    store.db.connect()
    rng = random.Random()
    mode = settings.execution_mode
    log.info("Sniper: mode=%s, slippage tolerance=%s", mode, settings.slippage_tolerance)

    for raw, data in broker.reliable_consume(EXECUTION_SIGNALS, INFLIGHT):
        try:
            if data.get("type") == "ARBITRAGE":
                handle_arbitrage(data, store)
            else:
                handle_value(data, mode=mode, tolerance=settings.slippage_tolerance,
                             store=store, broker=broker, rng=rng)
        except Exception as exc:  # noqa: BLE001
            log.error("Sniper error: %s", exc)
        finally:
            broker.ack(INFLIGHT, raw)


def main() -> None:
    log.info("Sniper Agent starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
