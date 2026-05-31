"""Analytics Engine — the brain.

Consumes ``market_signals``, computes a *modeled* win probability, prices the
expected value, and (when the edge clears the safety buffer and survives the
portfolio risk checks) emits an execution signal. It also feeds the arbitrage
book and logs every evaluation to ``market_history``.

Key behavioral change from the original: the Engine's probability comes only
from a trained :class:`ModelBundle`. If no model is loaded and
``strategy.require_model`` is set, the Engine logs the signal but **never
trades** — it does not fall back to a producer-supplied or random ``true_prob``.
"""
from __future__ import annotations

from typing import Optional

from ..broker import Broker, EXECUTION_SIGNALS, MARKET_SIGNALS
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..quant.arbitrage import ArbitrageEngine
from ..quant.models import ModelBundle, TeamStat
from ..quant.odds import calculate_ev, calculate_kelly_fraction
from ..quant.portfolio import PortfolioRiskManager

log = get_logger("engine")

MODEL_DIR = "models"
INFLIGHT = "market_signals:inflight"


def get_team_stat(db: Database, team_name: str) -> Optional[TeamStat]:
    if not db.available:
        return None
    try:
        row = db.query_one(
            "SELECT net_rating, pace FROM team_advanced_stats WHERE team_name ILIKE %s LIMIT 1",
            (f"%{team_name}%",),
        )
    except Exception:  # noqa: BLE001 - table may not be populated yet
        return None
    if not row:
        return None
    return TeamStat(net_rating=float(row[0]), pace=float(row[1]))


def model_probability(bundle: ModelBundle, db: Database, metadata: dict) -> Optional[float]:
    """Modeled win probability for the signal's participant, or None if unmodelable."""
    matchup = metadata.get("matchup")
    participant = metadata.get("participant")
    if not matchup or not participant or " @ " not in matchup:
        return None
    away_team, home_team = matchup.split(" @ ")
    return bundle.predict_participant_prob(
        home_team, away_team, participant,
        home_stat=get_team_stat(db, home_team),
        away_stat=get_team_stat(db, away_team),
    )


def detect_arbitrage(arb: ArbitrageEngine, market_id: str, odds: float, metadata: dict) -> Optional[dict]:
    matchup = metadata.get("matchup")
    participant = metadata.get("participant")
    if not matchup or not participant or " @ " not in matchup:
        return None
    away_team, home_team = matchup.split(" @ ")
    side = "Home" if participant == home_team else "Away"
    event_id = arb.update_odds(market_id, odds, metadata.get("source", "Unknown"), side)
    return arb.check_arbitrage(event_id) if event_id else None


def process_signal(data: dict, *, bundle, db, broker, arb, strategy) -> None:
    odds = data.get("odds")
    market_id = data.get("market_id", "unknown")
    metadata = data.get("metadata", {})

    # 1. Probability — modeled only.
    true_prob: Optional[float] = None
    if bundle is not None:
        true_prob = model_probability(bundle, db, metadata)
    if true_prob is None and not strategy.require_model:
        true_prob = data.get("true_prob")  # explicit opt-in to trust producers

    # 2. Arbitrage branch (independent of single-market EV).
    opp = detect_arbitrage(arb, market_id, odds, metadata)
    if opp:
        log.info("[ARBITRAGE] %.2f%% margin for %s", opp["profit_margin"] * 100, opp["event_id"])
        broker.push(EXECUTION_SIGNALS, {
            "type": "ARBITRAGE", "event_id": opp["event_id"],
            "margin": opp["profit_margin"], "legs": opp["legs"],
        })

    # 3. No modeled probability -> log nothing tradeable and stop.
    if true_prob is None:
        log.info("[ABSTAIN] %s: no modeled probability (no edge, no bet).", market_id)
        return

    ev = calculate_ev(true_prob, odds)
    if db.available:
        try:
            db.execute(
                "INSERT INTO market_history (market_id, odds, true_prob, ev) VALUES (%s, %s, %s, %s)",
                (market_id, float(odds), float(true_prob), float(ev)),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to log signal: %s", exc)

    # 4. Edge gate -> size -> risk -> emit.
    if ev <= strategy.safety_buffer_ev:
        log.info("[REJECT] %s | EV %.4f below buffer", market_id, ev)
        return

    fraction = calculate_kelly_fraction(ev, odds, strategy.kelly_multiplier)
    risk = PortfolioRiskManager(strategy)
    sized = risk.evaluate_risk(market_id, fraction, broker.active_trades())
    if sized <= 0:
        log.info("[RISK REJECT] %s | EV %.4f (portfolio constraints)", market_id, ev)
        return

    log.info("[SIGNAL] %s | EV %.4f | size %.4f", market_id, ev, sized)
    broker.push(EXECUTION_SIGNALS, {"market_id": market_id, "ev": ev, "fraction": sized, "odds": odds})


def run(settings: Settings) -> None:
    broker = Broker(settings.redis)
    db = Database(settings.db)
    db.connect()
    bundle = ModelBundle.load(MODEL_DIR)
    arb = ArbitrageEngine()
    strategy = settings.strategy
    log.info("Engine monitoring '%s' (EV buffer %.3f, require_model=%s)",
             MARKET_SIGNALS, strategy.safety_buffer_ev, strategy.require_model)

    for raw, data in broker.reliable_consume(MARKET_SIGNALS, INFLIGHT):
        try:
            process_signal(data, bundle=bundle, db=db, broker=broker, arb=arb, strategy=strategy)
        except Exception as exc:  # noqa: BLE001 - never let one bad signal kill the loop
            log.error("Error processing signal: %s", exc)
        finally:
            broker.ack(INFLIGHT, raw)


def main() -> None:
    log.info("Analytics Engine starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
