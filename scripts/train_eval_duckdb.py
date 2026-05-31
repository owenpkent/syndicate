"""Train and *out-of-sample* evaluate the win-probability model from DuckDB.

The model pipeline normally reads FINAL games from Postgres. This is the
DuckDB-backed path: it trains the real v2 model (shared ``walk_forward`` + feature
builder + standardizing logistic) directly on the tens of thousands of games in
``data/sportsball.duckdb`` — no server, no schema dance — and reports a proper
**chronological holdout** (fit on the earlier games, score the later ones), so the
numbers reflect generalization rather than the in-sample fit ``train.py`` prints.

It also compares the new 7-feature model against the old single-feature
(Elo-only) baseline, which is the honest "did this upgrade regress?" check.

With ``--write`` it persists the v2 artifacts to ``models/`` exactly as
``make retrain`` would, so the Engine can load them.

Usage:
    python scripts/train_eval_duckdb.py                 # train + holdout report
    python scripts/train_eval_duckdb.py --write         # also write models/
    python scripts/train_eval_duckdb.py --split 0.8 --db /tmp/copy.duckdb
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.pipelines._elo import walk_forward  # noqa: E402
from sportsball.quant import features as feat  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "sportsball.duckdb"
MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
HFA = 55.0
K = 22.0


def load_events(db_path: str) -> list[tuple]:
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute(
        """
        SELECT event_date, home_team, away_team, home_score, away_score
        FROM events WHERE home_score IS NOT NULL ORDER BY event_date ASC
        """
    ).fetchall()
    con.close()
    return rows


def _pipeline() -> Pipeline:
    return Pipeline([("scaler", StandardScaler()),
                     ("lr", LogisticRegression(max_iter=1000))])


def holdout_metrics(X: np.ndarray, y: np.ndarray, cols: list[int], split: float) -> dict:
    """Fit on the first ``split`` fraction (by row order = time), score the rest.

    ``cols`` selects the feature columns (e.g. ``[0]`` = Elo-only baseline). Pure:
    no I/O, so it unit-tests on synthetic arrays.
    """
    n = len(X)
    cut = int(n * split)
    Xc = X[:, cols]
    Xtr, ytr, Xte, yte = Xc[:cut], y[:cut], Xc[cut:], y[cut:]
    model = _pipeline().fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    return {
        "n_train": int(cut), "n_test": int(n - cut),
        "brier": float(brier_score_loss(yte, p)),
        "log_loss": float(log_loss(yte, p, labels=[0, 1])),
        "accuracy": float(accuracy_score(yte, (p >= 0.5).astype(int))),
    }


def _write_artifacts(rows, snapshots) -> None:
    X = np.array([r.features for r in rows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in rows])
    model = _pipeline().fit(X, y)
    MODEL_DIR.mkdir(exist_ok=True)
    (MODEL_DIR / "win_prob_model.pkl").write_bytes(pickle.dumps(model))
    (MODEL_DIR / "team_state.json").write_text(json.dumps({
        t: {"elo": s.elo, "last_game_date": s.last_game_date.isoformat() if s.last_game_date else None,
            "form": s.form, "games_played": s.games_played}
        for t, s in snapshots.items()
    }))
    (MODEL_DIR / "model_meta.json").write_text(json.dumps({
        "schema_version": feat.SCHEMA_VERSION, "feature_order": feat.FEATURE_ORDER,
        "n_features": feat.N_FEATURES, "hfa": HFA, "k_factor": K,
    }))


def main() -> None:
    ap = argparse.ArgumentParser(description="Train + out-of-sample eval from DuckDB")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--split", type=float, default=0.85)
    ap.add_argument("--write", action="store_true", help="persist v2 artifacts to models/")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"DuckDB {args.db} not found — run ingest_nba_duckdb.py first.")
        return
    rows_raw = load_events(args.db)
    print(f"Loaded {len(rows_raw)} FINAL games from {args.db}")

    frows, snapshots = walk_forward(rows_raw, K, HFA, mov_enabled=True,
                                    carry=0.75, gap_days=90, form_window=10)
    X = np.array([r.features for r in frows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in frows])

    base_rate = float(y[int(len(y) * args.split):].mean())
    v1 = holdout_metrics(X, y, cols=[0], split=args.split)               # Elo-only
    v2 = holdout_metrics(X, y, cols=list(range(feat.N_FEATURES)), split=args.split)  # full

    print("\nChronological holdout (fit earlier games, score later) "
          f"— test n={v2['n_test']}, home-win base rate {base_rate:.3f}")
    print(f"{'model':<22}{'brier':>10}{'log_loss':>12}{'accuracy':>12}")
    for name, m in (("v1 Elo-only (1 feat)", v1), ("v2 full (7 feat)", v2)):
        print(f"{name:<22}{m['brier']:>10.4f}{m['log_loss']:>12.4f}{m['accuracy']:>12.4f}")
    better = "v2 ✓" if v2["log_loss"] < v1["log_loss"] else "v1 (v2 regressed!)"
    print(f"\nLower log-loss is better → {better}")
    print("(net_rating/player_strength are 0 here — no team_advanced_stats in DuckDB; "
          "populate via Postgres + make fetch-stats/player-strength to activate them.)")

    if args.write:
        _write_artifacts(frows, snapshots)
        print(f"\nWrote v2 artifacts to {MODEL_DIR}/ (Engine-loadable).")


if __name__ == "__main__":
    main()
