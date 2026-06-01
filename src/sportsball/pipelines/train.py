"""Train the win-probability model from backfilled history.

Walks Elo forward with the optimized parameters, builds the full feature matrix
via the shared :mod:`sportsball.quant.features` contract, fits a standardizing
logistic Pipeline, and writes the three artifacts :class:`ModelBundle` loads:
``models/win_prob_model.pkl`` (the Pipeline), ``models/team_state.json`` (per-team
snapshots for symmetric serving), and ``models/model_meta.json`` (the feature
contract + hfa, so a stale-shaped artifact is rejected at load).
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..matching import normalize_team
from ..quant import calibration
from ..quant import features as feat
from ..store import Store
from ._elo import fetch_history, walk_forward

log = get_logger("train")
PARAMS_PATH = Path("optimized_params.json")
MODEL_DIR = Path("models")


def _roster_pit_map(store: Store) -> dict:
    """Point-in-time roster strength keyed by (normalized_team, date_iso).

    From the precomputed ``team_strength_pit`` table (``make roster-pit``). Empty
    when unavailable -> the roster feature contributes 0.
    """
    if not store.available:
        return {}
    try:
        rows = store.roster_pit_all()
    except Exception as exc:  # noqa: BLE001 - table may be absent
        log.warning("team_strength_pit unavailable (%s); roster feature -> 0.", exc)
        return {}
    out = {}
    for name, game_date, strength in rows:
        iso = game_date.date().isoformat() if hasattr(game_date, "date") else str(game_date)[:10]
        out[(normalize_team(name), iso)] = float(strength or 0.0)
    log.info("Loaded %d point-in-time roster values.", len(out))
    return out


def _availability_pit_map(store: Store) -> dict:
    """Point-in-time roster availability keyed by (normalized_team, date_iso).

    From the ``team_availability_pit`` table (``make ingest-injuries``). Empty when
    unavailable -> the availability feature contributes 0 (inert), so the model
    behaves exactly as it did before the feature existed until data lands.
    """
    if not store.available:
        return {}
    try:
        rows = store.availability_pit_all()
    except Exception as exc:  # noqa: BLE001 - table may be absent
        log.warning("team_availability_pit unavailable (%s); availability feature -> 0.", exc)
        return {}
    out = {}
    for name, game_date, availability in rows:
        iso = game_date.date().isoformat() if hasattr(game_date, "date") else str(game_date)[:10]
        out[(normalize_team(name), iso)] = float(availability or 0.0)
    log.info("Loaded %d point-in-time availability values.", len(out))
    return out


def _market_map(store: Store) -> dict:
    """No-vig market P(home) keyed by (home_token, away_token, date_iso).

    From ``events`` rows that carry real closing odds (``make ingest-odds``). Empty
    when unavailable -> the market feature contributes 0 (inert), so the model is
    unchanged until closing lines are loaded. This is Benter's lever: the market
    line as a model input, not just the EV benchmark.
    """
    from ..quant.odds import devig_two_way
    if not store.available:
        return {}
    try:
        rows = store.events_with_closing_odds()
    except Exception as exc:  # noqa: BLE001 - column/table may be absent
        log.warning("closing odds unavailable (%s); market feature -> 0.", exc)
        return {}
    out = {}
    for event_id, home, away, event_date, home_close, away_close, *_ in rows:
        p = devig_two_way(float(home_close or 0), float(away_close or 0))
        if p is None:
            continue
        iso = event_date.date().isoformat() if hasattr(event_date, "date") else str(event_date)[:10]
        out[(normalize_team(home), normalize_team(away), iso)] = p
    log.info("Loaded %d no-vig market probabilities.", len(out))
    return out


def _fit_calibration(X, y, cal_frac: float = 0.1) -> dict:
    """Fit a calibration spec on a held-out recent tail (auto: temperature vs isotonic).

    Trains a probe model on the earlier games and calibrates against its predictions
    on the most recent ``cal_frac`` (the slice closest to the serving distribution).
    The final model is still fit on ALL data; only the calibrator is estimated
    out-of-fold. Returns the ``identity`` spec when there's too little data.
    """
    cut = int(len(X) * (1 - cal_frac))
    if cut < 100 or len(X) - cut < 50:
        return {"method": "identity"}
    probe = Pipeline([("scaler", StandardScaler()),
                      ("lr", LogisticRegression(max_iter=1000))]).fit(X[:cut], y[:cut])
    p = probe.predict_proba(X[cut:])[:, 1]
    return calibration.fit(p, y[cut:], method="auto")


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

    strategy = load_settings().strategy
    store = Store(db)
    roster_pit = _roster_pit_map(store)
    availability_pit = _availability_pit_map(store)
    market_pit = _market_map(store)

    rows, snapshots = walk_forward(
        results, params["k_factor"], params["hfa"],
        mov_enabled=strategy.elo_mov_enabled, carry=strategy.elo_carry,
        gap_days=strategy.elo_offseason_gap_days, form_window=strategy.form_window,
        roster_pit=roster_pit, availability_pit=availability_pit, market_pit=market_pit,
    )
    X = np.array([r.features for r in rows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in rows])

    log.info("Training logistic Pipeline on %d samples × %d features...", len(X), feat.N_FEATURES)
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(max_iter=1000)),
    ]).fit(X, y)
    log.info("In-sample accuracy: %.4f", model.score(X, y))
    calib = _fit_calibration(X, y)
    temperature = float(calib.get("temperature", 1.0))  # back-compat surface
    log.info("Calibration: %s", calib.get("method"))

    MODEL_DIR.mkdir(exist_ok=True)
    (MODEL_DIR / "win_prob_model.pkl").write_bytes(pickle.dumps(model))
    (MODEL_DIR / "team_state.json").write_text(json.dumps({
        team: {
            "elo": s.elo,
            "last_game_date": s.last_game_date.isoformat() if s.last_game_date else None,
            "form": s.form,
            "games_played": s.games_played,
            "net_eff": s.net_eff,
            "roster": s.roster,
            "season": s.season,
            "availability": s.availability,
        } for team, s in snapshots.items()
    }))
    (MODEL_DIR / "model_meta.json").write_text(json.dumps({
        "schema_version": feat.SCHEMA_VERSION,
        "feature_order": feat.FEATURE_ORDER,
        "n_features": feat.N_FEATURES,
        "hfa": params["hfa"],
        "k_factor": params["k_factor"],
        "temperature": temperature,
        "calibration": calib,
        "mov_enabled": strategy.elo_mov_enabled,
        "carry": strategy.elo_carry,
        "gap_days": strategy.elo_offseason_gap_days,
        "form_window": strategy.form_window,
    }))
    log.info("Saved model + team_state + meta to %s/", MODEL_DIR)
    return True


def main() -> None:
    run(Database(load_settings().db))


if __name__ == "__main__":
    main()
