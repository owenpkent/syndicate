"""Walk-forward betting backtest with realistic questions — and honest caveats.

The free NBA data has scores but **no market odds**, so we can't bet against real
historical lines (the CLV gap). Instead we simulate a market and bracket reality:

* **naive market** — prices each game from an Elo-only model (public, weak). Our
  full model's extra signal *should* beat it -> an optimistic ceiling.
* **efficient market** — prices at our own best estimate (the model itself). By
  construction there's no edge, so you simply pay the vig -> a realistic floor.

Reality (a sharp sportsbook) sits between, much closer to efficient. We report
both, at 0% and a realistic ~4.5% vig, with proper **train/test separation**
(fit on the earlier games, bet only on the later holdout) and walk-forward Elo so
there is no look-ahead. Metrics are the ones a bettor actually asks: ROI, win
rate, number of bets, and max drawdown.

    python scripts/backtest.py [--split 0.7] [--vig 0.045] [--kelly 0.25] [--buffer 0.02]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.pipelines._elo import _coerce_date, walk_forward  # noqa: E402
from sportsball.quant import features as feat  # noqa: E402
from sportsball.quant.odds import calculate_ev, calculate_kelly_fraction  # noqa: E402

import measure_features as mf  # noqa: E402
import train_eval_duckdb as ted  # noqa: E402

START_BANKROLL = 1000.0


def _fit(X, y):
    return Pipeline([("s", StandardScaler()), ("lr", LogisticRegression(max_iter=1000))]).fit(X, y)


def _temperature(p, y, val=0.1):
    """Fit a 1-param temperature on a tail of the training predictions."""
    from scipy.optimize import minimize_scalar
    from sklearn.metrics import log_loss
    cut = int(len(p) * (1 - val))
    if cut < 50 or len(p) - cut < 20:
        return 1.0
    logit = np.log(np.clip(p[cut:], 1e-6, 1 - 1e-6) / (1 - np.clip(p[cut:], 1e-6, 1 - 1e-6)))
    yc = y[cut:]
    r = minimize_scalar(lambda T: log_loss(yc, 1 / (1 + np.exp(-logit / T)), labels=[0, 1]),
                        bounds=(0.5, 5), method="bounded")
    return float(r.x)


def run_bets(p_us, p_mkt, outcomes, *, vig, kelly, buffer, seasons=None) -> list[dict]:
    """Per-bet records: bet home or away (whichever has +EV above buffer) at
    vig-loaded market odds. Each record carries season/odds/stake/pnl/win so the
    same run can be sliced different ways. Order is chronological (for drawdown)."""
    seasons = seasons if seasons is not None else [None] * len(outcomes)
    out: list[dict] = []
    for pu, pm, won, season in zip(p_us, p_mkt, outcomes, seasons):
        for p_side, q_side, win in ((pu, pm, won), (1 - pu, 1 - pm, 1 - won)):
            # Vig-loaded decimal odds for this side (book implied probs sum to 1+vig).
            odds = 1.0 / max(q_side * (1 + vig), 1e-9)
            ev = calculate_ev(p_side, odds)
            if ev <= buffer:
                continue
            stake = START_BANKROLL * calculate_kelly_fraction(ev, odds, kelly)
            if stake <= 0:
                continue
            out.append({"season": season, "odds": odds, "stake": stake,
                        "pnl": stake * (odds - 1) if win == 1 else -stake, "win": int(win)})
    return out


def aggregate(records: list[dict]) -> dict:
    """ROI / win% / drawdown / final bankroll from a list of bet records.

    **Flat-Kelly** staking (stake = Kelly fraction of a *constant* base bankroll):
    compounding the live bankroll over thousands of bets explodes mathematically
    and isn't how a real bettor sizes. ROI (profit / turnover) is the headline.
    """
    staked = sum(r["stake"] for r in records)
    pnl = sum(r["pnl"] for r in records)
    bankroll, peak, max_dd = START_BANKROLL, START_BANKROLL, 0.0
    for r in records:
        bankroll += r["pnl"]
        peak = max(peak, bankroll)
        max_dd = max(max_dd, (peak - bankroll) / peak if peak > 0 else 0.0)
    return {"bankroll": START_BANKROLL + pnl, "roi": pnl / staked if staked else 0.0,
            "bets": len(records), "win_rate": sum(r["win"] for r in records) / len(records)
            if records else 0.0, "max_dd": max_dd}


def simulate(p_us, p_mkt, outcomes, *, vig, kelly, buffer, seasons=None) -> dict:
    """Convenience: run the bets and aggregate to summary metrics."""
    return aggregate(run_bets(p_us, p_mkt, outcomes, vig=vig, kelly=kelly,
                              buffer=buffer, seasons=seasons))


def _roi_view(title, groups: dict):
    """Print ROI/bets/win% per group (group -> list of bet records)."""
    print(f"\n{title}")
    print(f"{'group':<14}{'bets':>8}{'win%':>7}{'ROI':>9}{'maxDD':>8}")
    for key in sorted(groups):
        m = aggregate(groups[key])
        if not m["bets"]:
            continue
        print(f"{str(key):<14}{m['bets']:>8}{m['win_rate']*100:>6.1f}%"
              f"{m['roi']*100:>8.2f}%{m['max_dd']*100:>7.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward betting backtest")
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--vig", type=float, default=0.045)
    ap.add_argument("--kelly", type=float, default=0.25)
    ap.add_argument("--buffer", type=float, default=0.02)
    ap.add_argument("--analyze", action="store_true",
                    help="robustness: per-season ROI, EV-buffer sweep, odds buckets")
    args = ap.parse_args()

    tmp = Path(tempfile.gettempdir()) / "backtest.duckdb"
    shutil.copy(ted.DEFAULT_DB, tmp)
    rows_raw = ted.load_events(str(tmp))
    tmp.unlink(missing_ok=True)
    roster_pit = mf.roster_pit_from_pg()

    frows, _ = walk_forward(rows_raw, ted.K, ted.HFA, mov_enabled=True, carry=0.75,
                            gap_days=90, form_window=10, roster_pit=roster_pit)
    X = np.array([r.features for r in frows])
    y = np.array([1 if r.actual >= 1.0 else 0 for r in frows])
    cut = int(len(X) * args.split)
    Xtr, ytr, Xte, yte = X[:cut], y[:cut], X[cut:], y[cut:]
    seasons = [feat.season_of(_coerce_date(rows_raw[i][0], i)) for i in range(cut, len(rows_raw))]
    print(f"{len(rows_raw)} games | train {cut} | bet on {len(Xte)} holdout games\n")

    # Our full model (calibrated) and the "naive market" Elo-only model.
    full = _fit(Xtr, ytr)
    p_tr = full.predict_proba(Xtr)[:, 1]
    T = _temperature(p_tr, ytr)
    p_full = full.predict_proba(Xte)[:, 1]
    logit = np.log(np.clip(p_full, 1e-6, 1 - 1e-6) / (1 - np.clip(p_full, 1e-6, 1 - 1e-6)))
    p_us = 1 / (1 + np.exp(-logit / T))

    elo_only = _fit(Xtr[:, [0]], ytr)
    p_naive = elo_only.predict_proba(Xte[:, [0]])[:, 1]

    markets = {"naive (Elo-only book)": p_naive, "efficient (book = our model)": p_us}
    print(f"Kelly={args.kelly}  EV buffer={args.buffer}  start bankroll=${START_BANKROLL:.0f}")
    print(f"{'market':<28}{'vig':>6}{'bets':>7}{'win%':>7}{'ROI':>9}{'final $':>11}{'maxDD':>8}")
    for name, p_mkt in markets.items():
        for vig in (0.0, args.vig):
            m = simulate(p_us, p_mkt, yte, vig=vig, kelly=args.kelly, buffer=args.buffer, seasons=seasons)
            print(f"{name:<28}{vig*100:>5.1f}%{m['bets']:>7}{m['win_rate']*100:>6.1f}%"
                  f"{m['roi']*100:>8.2f}%{m['bankroll']:>11.0f}{m['max_dd']*100:>7.1f}%")

    print("\nReading this honestly: the 'naive' rows are an optimistic ceiling (assumes a "
          "book that only knows Elo); the 'efficient' rows are the realistic floor — against "
          "a sharp book you pay the vig. A real sportsbook sits much closer to efficient.")

    if not args.analyze:
        return

    # Robustness on the naive-market book at the realistic vig — is the edge
    # consistent across seasons, selective thresholds, and odds ranges?
    print("\n" + "=" * 60 + f"\nROBUSTNESS (naive book, vig={args.vig*100:.1f}%)")
    recs = run_bets(p_us, p_naive, yte, vig=args.vig, kelly=args.kelly,
                    buffer=args.buffer, seasons=seasons)

    by_season: dict = {}
    for r in recs:
        by_season.setdefault(r["season"], []).append(r)
    _roi_view("Per-season ROI:", by_season)

    print("\nEV-buffer sweep (selectivity):")
    print(f"{'buffer':<14}{'bets':>8}{'win%':>7}{'ROI':>9}{'maxDD':>8}")
    for buf in (0.0, 0.02, 0.05, 0.10, 0.20):
        m = simulate(p_us, p_naive, yte, vig=args.vig, kelly=args.kelly, buffer=buf, seasons=seasons)
        print(f"{buf:<14.2f}{m['bets']:>8}{m['win_rate']*100:>6.1f}%"
              f"{m['roi']*100:>8.2f}%{m['max_dd']*100:>7.1f}%")

    def odds_bucket(o):
        return ("fav <1.5" if o < 1.5 else "1.5-2.0" if o < 2.0 else
                "2.0-3.0" if o < 3.0 else "dog 3.0+")
    by_odds: dict = {}
    for r in recs:
        by_odds.setdefault(odds_bucket(r["odds"]), []).append(r)
    _roi_view("ROI by odds bucket (where the edge lives):", by_odds)


if __name__ == "__main__":
    main()
