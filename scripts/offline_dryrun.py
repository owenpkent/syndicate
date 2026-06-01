"""Offline end-to-end dry run on a complete SYNTHETIC season — no data needed.

Real NBA/odds data is often unreachable (network policy, no key, no DB). This
exercises the *real* modeling code — the shared ``walk_forward`` + feature
builder, the DuckDB train/eval path, the betting backtest, and the closing-odds
ingest parser/store — on a full, internally-consistent synthetic dataset whose
outcomes genuinely depend on point-in-time availability. It proves the plumbing
works on a complete dataset (not just unit fakes) and quantifies the v3
availability feature's lift.

Everything here is SYNTHETIC: the numbers validate the pipeline, not any edge.

    python scripts/offline_dryrun.py            # report + write data/sportsball.duckdb
    python scripts/offline_dryrun.py --keep-db  # leave the DuckDB for `make backtest`
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import backtest as bt  # noqa: E402  (real betting backtest functions)
import train_eval_duckdb as ted  # noqa: E402  (real DuckDB holdout metrics)
from sportsball.pipelines._elo import walk_forward  # noqa: E402
from sportsball.pipelines.ingest_odds import apply_closing_odds, parse_file_feed  # noqa: E402
from sportsball.quant import features as feat  # noqa: E402
from sportsball.quant.odds import calculate_ev  # noqa: E402
from sportsball.store import HOME, Store  # noqa: E402

from synth import make_season  # noqa: E402
from fakes import FakeDB  # noqa: E402

K, HFA, SPLIT, VIG = 22.0, 55.0, 0.8, 0.045
AVAIL_IDX = feat.FEATURE_ORDER.index("availability_diff")


def _write_duckdb(results, db_path: Path) -> None:
    import duckdb
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE events (event_date DATE, home_team TEXT, away_team TEXT, "
                "home_score INT, away_score INT)")
    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    [(d, h, a, hs, as_) for d, h, a, hs, as_ in results])
    con.close()


def _holdout_table(X, y) -> dict:
    elo = ted.holdout_metrics(X, y, cols=[0], split=SPLIT)
    no_av = ted.holdout_metrics(X, y, cols=list(range(AVAIL_IDX)), split=SPLIT)
    full = ted.holdout_metrics(X, y, cols=list(range(feat.N_FEATURES)), split=SPLIT)
    print("\nChronological holdout (fit earlier games, score later) "
          f"— test n={full['n_test']}")
    print(f"{'model':<28}{'brier':>10}{'log_loss':>12}{'accuracy':>11}")
    for name, m in (("Elo-only (1 feat)", elo),
                    ("v3 minus availability (7)", no_av),
                    ("v3 full (8 feat)", full)):
        print(f"{name:<28}{m['brier']:>10.4f}{m['log_loss']:>12.4f}{m['accuracy']:>11.4f}")
    d_ll = no_av["log_loss"] - full["log_loss"]
    d_br = no_av["brier"] - full["brier"]
    print(f"\nAvailability feature lift: Δlog-loss {d_ll:+.4f}, ΔBrier {d_br:+.4f} "
          f"({'adds signal ✓' if d_ll > 0 else 'no lift'})")
    return full


def _bet_and_clv(X, y, results) -> None:
    """Train a calibrated model, then (1) run the real betting backtest vs a naive
    book and (2) ingest synthetic closing odds and report CLV — both on real code."""
    cut = int(len(X) * SPLIT)
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
    full = bt._fit(Xtr, ytr)
    T = bt._temperature(full.predict_proba(Xtr)[:, 1], ytr)
    p = full.predict_proba(Xte)[:, 1]
    logit = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    p_us = 1 / (1 + np.exp(-logit / T))                      # our calibrated model
    p_naive = bt._fit(Xtr[:, [0]], ytr).predict_proba(Xte[:, [0]])[:, 1]  # soft book

    print("\nBetting backtest (real scripts/backtest.run_bets) — SYNTHETIC market")
    print(f"{'book':<22}{'vig':>6}{'bets':>7}{'win%':>7}{'ROI':>9}{'final$':>10}")
    for name, p_mkt in (("naive (Elo-only)", p_naive), ("efficient (=our model)", p_us)):
        for vig in (0.0, VIG):
            m = bt.simulate(p_us, p_mkt, yte, vig=vig, kelly=0.25, buffer=0.02)
            print(f"{name:<22}{vig*100:>5.1f}%{m['bets']:>7}{m['win_rate']*100:>6.1f}%"
                  f"{m['roi']*100:>8.2f}%{m['bankroll']:>10.0f}")

    # Closing-odds ingest path: build a synthetic closing line (efficient + vig),
    # run the REAL parser + store update, then report CLV of the soft price taken.
    holdout = results[cut:]
    records, taken, closing = [], [], []
    for (d, h, a, _hs, _as), p_eff, p_soft in zip(holdout, p_us, p_naive):
        hc = round(1 / max(p_eff * (1 + VIG), 1e-9), 4)
        ac = round(1 / max((1 - p_eff) * (1 + VIG), 1e-9), 4)
        records.append({"sport": "nba", "date": d.isoformat(),
                        "home_team": h, "away_team": a, "home_close": hc, "away_close": ac})
        taken.append(1 / max(p_soft * (1 + VIG), 1e-9))   # price the soft book offered (home)
        closing.append(hc)
    feed = Path(tempfile.gettempdir()) / "synth_odds.json"
    feed.write_text(json.dumps(records))
    rows = parse_file_feed(json.loads(feed.read_text()))   # real parser
    store = Store(FakeDB(available=True))
    n = apply_closing_odds(store, rows)                    # real store.update_closing_odds
    updates = sum(1 for s, _ in store.db.executed if "UPDATE events SET home_close" in s)
    clv = np.array(taken) / np.array(closing) - 1          # tools/clv.py definition
    print(f"\nClosing-odds ingest: parsed {len(rows)} games, issued {updates} UPDATEs "
          f"(real ingest_odds + store).")
    print(f"CLV of the soft (home) price vs close: avg {clv.mean()*100:+.2f}%, "
          f"beat-rate {(clv > 0).mean()*100:.1f}%  [make clv lights up on this data]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Offline synthetic end-to-end dry run")
    ap.add_argument("--db", default=str(ted.DEFAULT_DB))
    ap.add_argument("--keep-db", action="store_true",
                    help="leave the synthetic DuckDB so `make backtest`/`eval-duckdb` can read it")
    args = ap.parse_args()

    rng = np.random.default_rng(0)
    results, availability_pit = make_season(rng, n_teams=14, n_games=6000)
    print(f"[SYNTHETIC] generated {len(results)} games, "
          f"{len(availability_pit)} team-game availability points")

    rows, _ = walk_forward(results, K, HFA, mov_enabled=True, carry=0.75,
                           gap_days=90, form_window=10, availability_pit=availability_pit)
    X = np.array([r.features for r in rows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in rows])

    _holdout_table(X, y)
    _bet_and_clv(X, y, results)

    db_path = Path(args.db)
    _write_duckdb(results, db_path)
    print(f"\nWrote synthetic DuckDB → {db_path}")
    print("Real entrypoints now run on it:")
    print(f"  ./venv/bin/python scripts/train_eval_duckdb.py --db {db_path}")
    print("  ./venv/bin/python scripts/backtest.py        (reads data/sportsball.duckdb)")
    if not args.keep_db and str(db_path) == str(ted.DEFAULT_DB):
        pass  # leave at default path for the follow-up CLI runs


if __name__ == "__main__":
    main()
