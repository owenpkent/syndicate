"""Analytics Engine — the brain.

Consumes ``market_signals``, computes a *modeled* win probability, prices the
expected value, and (when the edge clears the safety buffer and survives the
portfolio risk checks) emits an execution signal. It also feeds the arbitrage
book and logs every modeled evaluation to ``signals``.

Two behaviors worth noting:

* Probability comes only from a trained :class:`ModelBundle`. With no model and
  ``strategy.require_model`` set, the Engine logs and **never trades** — it does
  not fall back to a producer-supplied or random ``true_prob``.
* The Engine resolves ``event_id``/``side`` once (it has the matchup) and stamps
  them onto the execution signal, so the Sniper/Settlement never re-parse a
  ``market_id`` or run a ``LIKE`` join.
"""
from __future__ import annotations

import os
from typing import Optional

from ..broker import Broker, EXECUTION_SIGNALS, MARKET_SIGNALS
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..quant.arbitrage import ArbitrageEngine
from ..quant.models import ModelBundle, TeamStat
from ..quant.odds import calculate_ev, calculate_kelly_fraction
from ..quant.portfolio import PortfolioRiskManager
from ..store import HOME, Store, parse_market_id, side_for

log = get_logger("engine")

MODEL_DIR = "models"
MODEL_FILE = os.path.join(MODEL_DIR, "win_prob_model.pkl")
INFLIGHT = "market_signals:inflight"


def _model_mtime() -> float:
    """Modification time of the model file, or 0.0 if absent."""
    try:
        return os.path.getmtime(MODEL_FILE)
    except OSError:
        return 0.0


def _teams(metadata: dict) -> Optional[tuple[str, str]]:
    matchup = metadata.get("matchup")
    if not matchup or " @ " not in matchup:
        return None
    away, home = matchup.split(" @ ")
    return home, away


def _team_stat(store: Store, team_name: str) -> Optional[TeamStat]:
    if not store.available:
        return None
    try:
        row = store.team_stat(team_name)
    except Exception:  # noqa: BLE001 - table may be empty/absent
        return None
    return TeamStat(net_rating=float(row[0]), pace=float(row[1])) if row else None


def model_probability(bundle: ModelBundle, store: Store, metadata: dict) -> Optional[float]:
    teams = _teams(metadata)
    participant = metadata.get("participant")
    if not teams or not participant:
        return None
    home, away = teams
    return bundle.predict_participant_prob(
        home, away, participant,
        home_stat=_team_stat(store, home), away_stat=_team_stat(store, away),
    )


def process_signal(data: dict, *, bundle, store, broker, arb, strategy) -> None:
    odds = data.get("odds")
    market_id = data.get("market_id", "unknown")
    metadata = data.get("metadata", {})
    teams = _teams(metadata)

    # 1. Arbitrage branch (independent of single-market EV).
    if teams and metadata.get("participant"):
        home, away = teams
        side_label = "Home" if metadata["participant"] == home else "Away"
        event_id = arb.update_odds(market_id, odds, metadata.get("source", "Unknown"), side_label)
        opp = arb.check_arbitrage(event_id) if event_id else None
        if opp:
            log.info("[ARBITRAGE] %.2f%% margin for %s", opp["profit_margin"] * 100, opp["event_id"])
            broker.push(EXECUTION_SIGNALS, {
                "type": "ARBITRAGE", "event_id": opp["event_id"],
                "margin": opp["profit_margin"], "legs": opp["legs"],
            })

    # 2. Probability — modeled only.
    true_prob: Optional[float] = None
    if bundle is not None:
        true_prob = model_probability(bundle, store, metadata)
    if true_prob is None and not strategy.require_model:
        true_prob = data.get("true_prob")

    if true_prob is None or not teams:
        log.info("[ABSTAIN] %s: no modeled probability (no edge, no bet).", market_id)
        return

    home, away = teams
    participant = metadata["participant"]
    side = side_for(participant, home)
    ev = calculate_ev(true_prob, odds)

    # 3. Persist the evaluation (ensure the event row exists first for the FK).
    if store.available:
        try:
            _, event_id, _ = parse_market_id(market_id)
            store.upsert_event_stub(event_id, home, away)
            store.record_signal(event_id, side, metadata.get("source"), odds, true_prob, ev)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to persist signal: %s", exc)
            event_id = None
    else:
        event_id = None

    # 4. Edge gate -> size -> risk -> emit.
    if ev <= strategy.safety_buffer_ev:
        log.info("[REJECT] %s | EV %.4f below buffer", market_id, ev)
        return

    fraction = calculate_kelly_fraction(ev, odds, strategy.kelly_multiplier)
    sized = PortfolioRiskManager(strategy).evaluate_risk(market_id, fraction, broker.active_trades())
    if sized <= 0:
        log.info("[RISK REJECT] %s | EV %.4f (portfolio constraints)", market_id, ev)
        return

    log.info("[SIGNAL] %s | EV %.4f | size %.4f", market_id, ev, sized)
    broker.push(EXECUTION_SIGNALS, {
        "market_id": market_id, "event_id": event_id or parse_market_id(market_id)[1],
        "side": side, "home_team": home, "away_team": away,
        "source": metadata.get("source"), "ev": ev, "fraction": sized, "odds": odds,
    })


def run(settings: Settings) -> None:
    broker = Broker(settings.redis)
    store = Store(Database(settings.db))
    store.db.connect()
    bundle = ModelBundle.load(MODEL_DIR)
    model_mtime = _model_mtime()
    arb = ArbitrageEngine()
    strategy = settings.strategy
    log.info("Engine monitoring '%s' (EV buffer %.3f, require_model=%s)",
             MARKET_SIGNALS, strategy.safety_buffer_ev, strategy.require_model)

    for raw, data in broker.reliable_consume(MARKET_SIGNALS, INFLIGHT):
        # Hot-reload the model if the retrainer has written a newer one.
        current_mtime = _model_mtime()
        if current_mtime > model_mtime:
            log.info("Detected updated model; reloading.")
            bundle = ModelBundle.load(MODEL_DIR) or bundle
            model_mtime = current_mtime
        try:
            process_signal(data, bundle=bundle, store=store, broker=broker, arb=arb, strategy=strategy)
        except Exception as exc:  # noqa: BLE001 - never let one bad signal kill the loop
            log.error("Error processing signal: %s", exc)
        finally:
            broker.ack(INFLIGHT, raw)


def main() -> None:
    log.info("Analytics Engine starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
