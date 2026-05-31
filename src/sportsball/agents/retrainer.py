"""Retrainer Agent — periodically rebuilds the model from fresh history.

Runs the optimize → train loop every ``RETRAIN_INTERVAL`` seconds (default
daily). The Analytics Engine hot-reloads the new ``models/win_prob_model.pkl``
on its next loop iteration, so ratings stay current as games settle without a
manual restart.
"""
from __future__ import annotations

import time

from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..pipelines import retrain

log = get_logger("retrainer")


def run(settings: Settings) -> None:
    db = Database(settings.db)
    interval = settings.retrain_interval
    log.info("Retrainer starting (interval %ds).", interval)
    while True:
        try:
            retrain.run(db)
        except Exception as exc:  # noqa: BLE001
            log.error("Retrain loop error: %s", exc)
        time.sleep(interval)


def main() -> None:
    run(load_settings())


if __name__ == "__main__":
    main()
