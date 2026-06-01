"""Measure the out-of-sample lift of each feature group (ablation).

Trains the real model on DuckDB game history, threading in the point-in-time
roster strength + availability from Postgres (``team_strength_pit`` /
``team_availability_pit``) and the no-vig closing line from the DuckDB
``home/away_close`` columns, then reports a chronological-holdout ablation:
Elo only -> + rest/back-to-back/form -> + net-eff -> + roster -> + availability
-> + market. This is the honest answer to "do the extra features earn their keep?".

Run after the PIT tables are populated: ``make roster-pit`` (roster),
``make ingest-injuries`` (availability), and ``make ingest-odds`` (closing line
into DuckDB via ``--duckdb``) so the last three rows aren't inert.

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

# Feature-group ablation by column index into features.FEATURE_ORDER. net_rating_diff,
# player_strength_diff, availability_diff are point-in-time (season-to-date, prior games
# only); market_logit is the no-vig closing-line logit. Indices:
# 0 elo_diff_hfa | 1 net_rating_diff (PIT net-eff) | 2 rest | 3 b2b_home | 4 b2b_away |
# 5 form | 6 player_strength_diff (PIT roster) | 7 availability_diff (PIT) | 8 market_logit
ABLATION = [
    ("elo only",            [0]),
    ("+ rest/b2b/form",     [0, 2, 3, 4, 5]),
    ("+ net-eff (PIT)",     [0, 2, 3, 4, 5, 1]),
    ("+ roster (PIT)",      [0, 1, 2, 3, 4, 5, 6]),
    ("+ availability (PIT)", [0, 1, 2, 3, 4, 5, 6, 7]),
    ("+ market (no-vig)",   [0, 1, 2, 3, 4, 5, 6, 7, 8]),
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


def availability_pit_from_pg() -> dict:
    """Point-in-time roster availability keyed by (normalized_team, date_iso)."""
    store = Store(Database(load_settings().db))
    if not store.available:
        print("WARNING: Postgres unavailable — availability feature will be 0.")
        return {}
    try:
        rows = store.availability_pit_all()
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: team_availability_pit unavailable ({exc}); availability feature 0. "
              "Run `make ingest-injuries`.")
        return {}
    out = {}
    for name, gd, availability in rows:
        iso = gd.date().isoformat() if hasattr(gd, "date") else str(gd)[:10]
        out[(normalize_team(name), iso)] = float(availability or 0.0)
    print(f"Loaded {len(out)} point-in-time availability values.")
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
    # load_events returns 7-tuples (…, home_close, away_close); walk_forward wants
    # 5-tuples, with the closing odds threaded in separately as market_pit.
    market_pit = ted.build_market_pit(rows_raw)
    results = [(d, h, a, hs, as_) for (d, h, a, hs, as_, _hc, _ac) in rows_raw]
    print(f"Loaded {len(results)} games ({len(market_pit)} with closing odds).")

    roster_pit = roster_pit_from_pg()
    availability_pit = availability_pit_from_pg()
    frows, _ = walk_forward(results, K, HFA, mov_enabled=True, carry=0.75,
                            gap_days=90, form_window=10, roster_pit=roster_pit,
                            availability_pit=availability_pit, market_pit=market_pit)
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
    print("\n(Δlog_loss vs 'elo only'; negative = better. net-eff/roster/availability are "
          "point-in-time season-to-date, leakage-free; market is the no-vig closing line. "
          "Rows are inert if their source table/odds aren't loaded.)")


if __name__ == "__main__":
    main()
