"""Retrain orchestration: optimize Elo params, then fit the model.

One call that runs the full modeling loop against current history, so it can be
triggered manually (`sportsball-retrain`) or on a schedule by the retrainer agent.
"""
from __future__ import annotations

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from . import optimize, train

log = get_logger("retrain")


def run(db: Database) -> bool:
    log.info("Retrain: optimizing Elo hyperparameters...")
    if optimize.run(db) is None:
        log.error("Retrain aborted: optimization produced no params (no data?).")
        return False
    log.info("Retrain: training win-probability model...")
    ok = train.run(db)
    log.info("Retrain %s.", "complete" if ok else "failed")
    return ok


def main() -> None:
    run(Database(load_settings().db))


if __name__ == "__main__":
    main()
