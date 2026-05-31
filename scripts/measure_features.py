"""Measure the out-of-sample lift of each feature group (ablation).

Trains the real model on DuckDB game history, injecting the net-rating /
roster-strength enrichment from Postgres ``team_advanced_stats`` (current-season
values, valid for the recent holdout), then reports a chronological-holdout
ablation: Elo only -> + rest/back-to-back/form -> + net-rating -> + player
strength. This is the honest answer to "do the extra features earn their keep?".

Run after ``make fetch-stats`` (+ ``make player-strength``) have populated
``team_advanced_stats``.

    python scripts/measure_features.py [--db data/sportsball.duckdb] [--split 0.85]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.config import load_settings  # noqa: E402
from sportsball.db import Database  # noqa: E402
from sportsball.matching import normalize_team  # noqa: E402
from sportsball.pipelines._elo import walk_forward  # noqa: E402
from sportsball.quant import features as feat  # noqa: E402
from sportsball.store import Store  # noqa: E402

import train_eval_duckdb as ted  # noqa: E402  (sibling script: load_events, holdout_metrics)

HFA, K = ted.HFA, ted.K

# Feature-group ablation by column index into features.FEATURE_ORDER. net_rating_diff
# and player_strength_diff are now point-in-time (season-to-date, prior games only).
# 0 elo_diff_hfa | 1 net_rating_diff (PIT net-eff) | 2 rest | 3 b2b_home | 4 b2b_away | 5 form | 6 player_strength_diff (PIT roster)
ABLATION = [
    ("elo only",            [0]),
    ("+ rest/b2b/form",     [0, 2, 3, 4, 5]),
    ("+ net-eff (PIT)",     [0, 2, 3, 4, 5, 1]),
    ("+ roster (PIT)",      [0, 1, 2, 3, 4, 5, 6]),
]


def roster_pit_from_pg() -> dict:
    """Point-in-time roster strength keyed by (normalized_team, date_iso)."""
    store = Store(Database(load_settings().db))
    if not store.available:
        print("WARNING: Postgres unavailable — roster feature will be 0.")
        return {}
    try:
        rows = store.roster_pit_all()
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: team_strength_pit unavailable ({exc}); roster feature 0. Run `make roster-pit`.")
        return {}
    out = {}
    for name, gd, strength in rows:
        iso = gd.date().isoformat() if hasattr(gd, "date") else str(gd)[:10]
        out[(normalize_team(name), iso)] = float(strength or 0.0)
    print(f"Loaded {len(out)} point-in-time roster values.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Feature-ablation holdout measurement")
    ap.add_argument("--db", default=str(ted.DEFAULT_DB))
    ap.add_argument("--split", type=float, default=0.85)
    args = ap.parse_args()

    src = Path(args.db)
    if not src.exists():
        print(f"DuckDB {src} not found.")
        return
    # Copy so a running ingest's write-lock doesn't block our read.
    tmp = Path(tempfile.gettempdir()) / "measure_features.duckdb"
    shutil.copy(src, tmp)
    rows_raw = ted.load_events(str(tmp))
    tmp.unlink(missing_ok=True)
    print(f"Loaded {len(rows_raw)} games.")

    roster_pit = roster_pit_from_pg()
    frows, _ = walk_forward(rows_raw, K, HFA, mov_enabled=True, carry=0.75,
                            gap_days=90, form_window=10, roster_pit=roster_pit)
    X = np.array([r.features for r in frows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in frows])

    base = ABLATION[0][1]
    print(f"\nChronological holdout ablation (split={args.split}, "
          f"test n={int(len(X) * (1 - args.split))}):")
    print(f"{'feature set':<22}{'brier':>9}{'log_loss':>11}{'accuracy':>11}{'Δlog_loss':>12}")
    base_ll = None
    for name, cols in ABLATION:
        m = ted.holdout_metrics(X, y, cols, args.split)
        if base_ll is None:
            base_ll = m["log_loss"]
            delta = ""
        else:
            d = m["log_loss"] - base_ll
            delta = f"{d:+.4f}"
        print(f"{name:<22}{m['brier']:>9.4f}{m['log_loss']:>11.4f}"
              f"{m['accuracy']:>11.4f}{delta:>12}")
    print("\n(Δlog_loss vs 'elo only'; negative = better. net-eff/roster are point-in-time "
          "season-to-date, computed leakage-free across all history.)")


if __name__ == "__main__":
    main()
