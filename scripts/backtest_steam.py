"""Backtest the steam (line-movement) strategy with realistic execution + risk.

The validated edge: the total's move from open->close is informative. But you can't
bet the open knowing the close — you react *after* the move starts, capturing only
a FRACTION phi of it. Entry line = close - phi*(close-open): phi=1 is the open
(hindsight upper bound), phi=0 is the close (just the vig, losing). Realistic
chasing is phi ~ 0.3-0.6.

Reports proper risk metrics (not just ROI): max drawdown, Sharpe, longest losing
streak, on flat 1-unit stakes at -110, per sport, across a phi sweep. Honest about
the fact that the deployable edge is a fraction of the hindsight number.

    python scripts/backtest_steam.py [--minmove-sd 0.15]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
JUICE = 0.91  # -110 -> profit per 1 unit on a win


def _f(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None


def load(sport):
    out = []
    for r in json.load(open(DATA / f"{sport}_archive_10Y.json")):
        ot, ct = _f(r.get("open_over_under")), _f(r.get("close_over_under"))
        hf = _f(r.get("home_final")) if r.get("home_final") is not None else _f(r.get("home_score"))
        af = _f(r.get("away_final")) if r.get("away_final") is not None else _f(r.get("away_score"))
        if None in (ot, ct, hf, af):
            continue
        out.append((ot, ct, hf + af))
    return out


def simulate(games, phi, minmove):
    """Flat 1-unit bets; return per-bet pnl array (chronological)."""
    pnl = []
    for ot, ct, tot in games:
        move = ct - ot
        if abs(move) < minmove:
            continue
        L = ct - phi * move                  # entry line: phi of the way from close to open
        if tot == L:
            continue                          # push
        if move > 0:                          # Over steam
            win = tot > L
        else:                                 # Under steam
            win = tot < L
        pnl.append(JUICE if win else -1.0)
    return np.array(pnl)


def metrics(pnl):
    if len(pnl) == 0:
        return None
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    # longest losing streak
    streak = mx = 0
    for x in pnl:
        streak = streak + 1 if x < 0 else 0
        mx = max(mx, streak)
    return {
        "n": len(pnl), "win%": (pnl > 0).mean() * 100, "roi%": pnl.mean() * 100,
        "units": cum[-1], "maxDD": dd.max(),
        "sharpe": pnl.mean() / pnl.std() * np.sqrt(len(pnl)) if pnl.std() else 0,
        "streak": mx,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minmove-sd", type=float, default=0.12,
                    help="min move to bet, as a fraction of the sport's total SD")
    args = ap.parse_args()
    print("Steam backtest — flat 1u @ -110, entry = close - phi*(close-open).")
    print("phi=1 hindsight upper bound; realistic chasing ~0.3-0.6.\n")
    for sport in ("nba", "mlb", "nhl", "nfl"):
        f = DATA / f"{sport}_archive_10Y.json"
        if not f.exists():
            continue
        g = load(sport)
        sd = np.std([x[2] for x in g])
        mm = args.minmove_sd * sd
        print(f"=== {sport.upper()} ({len(g)} games, total SD {sd:.1f}, min move {mm:.1f}) ===")
        print(f"{'phi':>5}{'bets':>7}{'win%':>7}{'roi%':>7}{'units':>8}{'maxDD':>7}{'sharpe':>8}{'L-streak':>9}")
        for phi in (1.0, 0.6, 0.5, 0.4, 0.3, 0.0):
            m = metrics(simulate(g, phi, mm))
            if m:
                print(f"{phi:>5.1f}{m['n']:>7}{m['win%']:>7.1f}{m['roi%']:>7.2f}"
                      f"{m['units']:>8.0f}{m['maxDD']:>7.0f}{m['sharpe']:>8.1f}{m['streak']:>9}")
        print()


if __name__ == "__main__":
    main()
