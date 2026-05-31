"""Train the win-probability model from backfilled history.

Walks Elo forward with the optimized parameters, fits a logistic regression on
the rating differential, and writes ``models/win_prob_model.pkl`` +
``models/current_ratings.json`` — exactly what :class:`ModelBundle` loads.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ._elo import fetch_history, walk_forward

log = get_logger("train")
PARAMS_PATH = Path("optimized_params.json")
MODEL_DIR = Path("models")


def run(db: Database) -> bool:
    try:
        params = json.loads(PARAMS_PATH.read_text())
    except FileNotFoundError:
        log.error("%s not found. Run sportsball-optimize first.", PARAMS_PATH)
        return False

    results = fetch_history(db)
    if not results:
        log.error("No historical data found.")
        return False

    rows, ratings = walk_forward(results, params["k_factor"], params["hfa"])
    X = np.array([[diff] for diff, _exp, _actual in rows])
    y = np.array([1 if actual >= 1.0 else 0 for _diff, _exp, actual in rows])

    log.info("Training logistic regression on %d samples...", len(X))
    model = LogisticRegression().fit(X, y)
    log.info("In-sample accuracy: %.4f", model.score(X, y))

    MODEL_DIR.mkdir(exist_ok=True)
    (MODEL_DIR / "win_prob_model.pkl").write_bytes(pickle.dumps(model))
    (MODEL_DIR / "current_ratings.json").write_text(json.dumps(ratings))
    log.info("Saved model + ratings to %s/", MODEL_DIR)
    return True


def main() -> None:
    run(Database(load_settings().db))


if __name__ == "__main__":
    main()
