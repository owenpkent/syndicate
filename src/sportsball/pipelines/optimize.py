"""Optimize Elo hyperparameters (K-factor, home-field advantage) by log-loss."""
from __future__ import annotations

import json
from pathlib import Path

from scipy.optimize import minimize

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ._elo import fetch_history, mean_log_loss

log = get_logger("optimize")
PARAMS_PATH = Path("optimized_params.json")


def run(db: Database) -> dict | None:
    results = fetch_history(db)
    if not results:
        log.error("No historical data. Run the backfill (or seed demo data) first.")
        return None
    log.info("Optimizing over %d games...", len(results))
    res = minimize(lambda p: mean_log_loss(results, *p), [20.0, 50.0],
                   method="L-BFGS-B", bounds=[(5, 100), (0, 200)])
    if not res.success:
        log.error("Optimization failed: %s", res.message)
        return None
    params = {"k_factor": float(res.x[0]), "hfa": float(res.x[1])}
    log.info("Optimal K=%.2f HFA=%.2f (log-loss %.4f)", params["k_factor"], params["hfa"], res.fun)
    PARAMS_PATH.write_text(json.dumps(params))
    return params


def main() -> None:
    run(Database(load_settings().db))


if __name__ == "__main__":
    main()
