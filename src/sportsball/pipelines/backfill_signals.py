"""Persist the trained model's predictions as ``signals`` for evaluation.

``make evaluate`` scores logged ``signals`` against FINAL ``events`` — but with no
live trading there are none. This walks every FINAL event, asks the loaded
``ModelBundle`` for its home win probability, and records it as a signal from
source ``BACKFILL``, so the evaluator reports the **real model's** Brier/log-loss
on real games rather than demo noise.

Idempotent: it clears prior ``BACKFILL`` signals first, so re-running after a
retrain reflects the new model.

Scope: only **recent** games (a trailing window from the latest game) are scored,
because the model's ``team_state`` snapshot is the *end-of-history* rating — valid
for current/upcoming games, not for replaying decades of history with today's
ratings. For a rigorous out-of-sample number across all history use the
walk-forward holdout (``make eval-duckdb``); this is an in-sample sanity check on
the current season.
"""
from __future__ import annotations

from datetime import timedelta

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..matching import parse_event_date
from ..quant.models import ModelBundle, TeamStat
from ..store import HOME, Store

log = get_logger("backfill_signals")

MODEL_DIR = "models"
SOURCE = "BACKFILL"
WINDOW_DAYS = 210  # ~one NBA season back from the latest game


def _team_stat(store: Store, team: str):
    try:
        row = store.team_stat(team)
    except Exception:  # noqa: BLE001 - table may be absent
        return None
    if not row:
        return None
    ps = float(row[2]) if len(row) > 2 and row[2] is not None else 0.0
    return TeamStat(net_rating=float(row[0]), pace=float(row[1]), player_strength=ps)


def run(db: Database, bundle: ModelBundle | None = None) -> int:
    store = Store(db)
    if not store.available:
        log.error("Database unavailable.")
        return 0
    bundle = bundle or ModelBundle.load(MODEL_DIR)
    if bundle is None:
        log.error("No (current) model in %s — run `make retrain` first.", MODEL_DIR)
        return 0

    store.clear_signals(SOURCE)
    max_row = store.max_event_date()
    since = None
    if max_row and max_row[0] is not None and hasattr(max_row[0], "__sub__"):
        since = max_row[0] - timedelta(days=WINDOW_DAYS)
        log.info("Scoring games since %s (latest %s).", since, max_row[0])
    n = 0
    for event_id, home, away, event_date in store.final_events(since):
        current_date = parse_event_date(event_id) or (
            event_date.date() if hasattr(event_date, "date") else None)
        p_home = bundle.predict_home_prob(
            home, away, current_date=current_date,
            home_stat=_team_stat(store, home), away_stat=_team_stat(store, away))
        p_home = min(max(p_home, 1e-4), 1 - 1e-4)
        odds = round(1.0 / p_home, 4)               # implied fair price (placeholder)
        store.record_signal(event_id, HOME, SOURCE, odds, p_home, p_home * odds - 1)
        n += 1
    log.info("Backfilled %d model signals (source=%s).", n, SOURCE)
    return n


def main() -> None:
    run(Database(load_settings().db))


if __name__ == "__main__":
    main()
