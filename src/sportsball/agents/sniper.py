"""Sniper Agent — the executioner.

Drains ``execution_signals`` and, in PAPER mode, simulates a fill with slippage,
persists the trade, and records the position's exposure in the ``active_trades``
hash (later cleared by the Settlement Agent's reaper).
"""
from __future__ import annotations

import random

from ..broker import Broker, EXECUTION_SIGNALS
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger

log = get_logger("sniper")

INFLIGHT = "execution_signals:inflight"


def simulate_fill(odds: float, tolerance: float, rng: random.Random) -> dict:
    """Simulate a paper fill with slippage drawn in [0.1%, 0.5%]."""
    slippage = rng.uniform(0.001, 0.005)
    if slippage > tolerance:
        return {"status": "FAILED", "reason": f"slippage {slippage:.4f} > tol {tolerance}"}
    return {"status": "SUCCESS", "executed_odds": round(odds * (1 - slippage), 4)}


def _persist(db: Database, market_id: str, odds: float, fraction: float, status: str) -> None:
    if not db.available:
        return
    try:
        db.execute(
            "INSERT INTO trade_history (market_id, executed_odds, fraction, status) VALUES (%s, %s, %s, %s)",
            (market_id, odds, fraction, status),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to persist trade: %s", exc)


def handle_arbitrage(data: dict, db: Database) -> None:
    log.info("[ARBITRAGE] executing %d legs for %s | margin %.2f%%",
             len(data["legs"]), data["event_id"], data["margin"] * 100)
    for leg in data["legs"]:
        log.info("  leg %s | odds %s | alloc %.4f", leg["market_id"], leg["odds"], leg["allocation"])
        _persist(db, leg["market_id"], leg["odds"], leg["allocation"], "ARBITRAGE_LEG")


def handle_value(data: dict, *, mode: str, tolerance: float, db: Database, broker: Broker,
                 rng: random.Random) -> None:
    market_id, odds, fraction = data["market_id"], data["odds"], data["fraction"]
    log.info("[TARGET] %s | odds %s | size %.4f", market_id, odds, fraction)

    if mode != "PAPER":
        log.info("[SKIP] mode=%s (live execution not implemented)", mode)
        _persist(db, market_id, 0, fraction, "SKIPPED")
        return

    result = simulate_fill(odds, tolerance, rng)
    if result["status"] == "SUCCESS":
        final = result["executed_odds"]
        log.info("[EXECUTE] SUCCESS %s | final odds %s", market_id, final)
        broker.set_exposure(market_id, fraction)
        _persist(db, market_id, final, fraction, "SUCCESS")
    else:
        log.info("[EXECUTE] REJECTED %s | %s", market_id, result["reason"])
        _persist(db, market_id, 0, fraction, "FAILED")


def run(settings: Settings) -> None:
    broker = Broker(settings.redis)
    db = Database(settings.db)
    db.connect()
    rng = random.Random()
    mode = settings.execution_mode
    log.info("Sniper: mode=%s, slippage tolerance=%s", mode, settings.slippage_tolerance)

    for raw, data in broker.reliable_consume(EXECUTION_SIGNALS, INFLIGHT):
        try:
            if data.get("type") == "ARBITRAGE":
                handle_arbitrage(data, db)
            else:
                handle_value(data, mode=mode, tolerance=settings.slippage_tolerance,
                             db=db, broker=broker, rng=rng)
        except Exception as exc:  # noqa: BLE001
            log.error("Sniper error: %s", exc)
        finally:
            broker.ack(INFLIGHT, raw)


def main() -> None:
    log.info("Sniper Agent starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
