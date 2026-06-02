"""Backtest predict-the-close: forecast the line's move, bet the OPEN (deployable).

The surviving market-modeling edge. At open we predict where the total will CLOSE
from open-time info (the opener + each team's recent scoring form, derived from the
archive itself — no external data). If we predict the line will move our way, we
bet the OPENING number. This is deployable (you legitimately bet the open with a
forecast — NO capture-fraction penalty, unlike chasing steam), and it's settled
against the actual result.

Runs on the free SBRO open/close archives (nba/mlb/nhl/nfl). Honest question: does
forecasting the move beat the -110 vig (52.4%) out-of-sample, across sports?

    python scripts/backtest_predict_close.py
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from numpy.linalg import lstsq

DATA = Path(__file__).resolve().parent.parent / "data"
JUICE = 0.91


def _f(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None


# plausible total-line bounds per sport (reject garbage open/close values)
BOUNDS = {"nba": (150, 280), "mlb": (5, 16), "nhl": (3, 9), "nfl": (25, 75)}


def load(sport):
    lo, hi = BOUNDS[sport]
    rows = []
    for r in json.load(open(DATA / f"{sport}_archive_10Y.json")):
        ot, ct = _f(r.get("open_over_under")), _f(r.get("close_over_under"))
        hf = _f(r.get("home_final")) if r.get("home_final") is not None else _f(r.get("home_score"))
        af = _f(r.get("away_final")) if r.get("away_final") is not None else _f(r.get("away_score"))
        if None in (ot, ct, hf, af) or not (r.get("home_team") and r.get("away_team") and r.get("date")):
            continue
        if not (lo < ot < hi and lo < ct < hi):       # drop garbage lines
            continue
        rows.append((int(r["date"]), r["home_team"], r["away_team"], ot, ct, hf, af))
    rows.sort(key=lambda x: x[0])
    return rows


def build(sport):
    games = load(sport)
    scored = defaultdict(lambda: deque(maxlen=8))   # team's recent points scored
    allowed = defaultdict(lambda: deque(maxlen=8))  # team's recent points allowed
    X, OT, CT, Y = [], [], [], []
    for _, h, a, ot, ct, hf, af in games:
        if scored[h] and scored[a]:
            feats = [ot,
                     np.mean(scored[h]), np.mean(allowed[h]),
                     np.mean(scored[a]), np.mean(allowed[a]),
                     np.mean(scored[h]) + np.mean(allowed[a]),   # crude expected total halves
                     np.mean(scored[a]) + np.mean(allowed[h])]
            X.append(feats); OT.append(ot); CT.append(ct); Y.append(hf + af)
        scored[h].append(hf); allowed[h].append(af)
        scored[a].append(af); allowed[a].append(hf)
    return np.array(X), np.array(OT), np.array(CT), np.array(Y)


def fit(ftr, fte, ytr):
    A = np.column_stack([ftr, np.ones(len(ftr))]); coef, *_ = lstsq(A, ytr, rcond=None)
    return np.column_stack([fte, np.ones(len(fte))]) @ coef


def main():
    print("Predict-the-close — bet the OPEN when we forecast a move (deployable, -110).")
    print("Beat 52.4% win to clear the vig.\n")
    for sport in ("nba", "mlb", "nhl", "nfl"):
        if not (DATA / f"{sport}_archive_10Y.json").exists():
            continue
        X, OT, CT, Y = build(sport)
        n = len(X); cut = int(n * 0.8)
        # train to predict the CLOSE from open + recent form
        pred = fit(np.column_stack([OT, X])[:cut], np.column_stack([OT, X])[cut:], CT[:cut])
        diff = pred - OT[cut:]; yt = Y[cut:]; ot = OT[cut:]; ct = CT[cut:]
        # move-prediction quality
        mv = CT - OT; pm = fit(X[:cut], X[cut:], mv[:cut]); mte = mv[cut:]
        r2 = 1 - ((mte - pm) ** 2).sum() / ((mte - mte.mean()) ** 2).sum()
        move_sd = (CT - OT).std()
        print(f"=== {sport.upper()} ({n} games) | move SD {move_sd:.2f} | move-pred OOS R^2 {r2:+.4f} ===")
        print(f"{'thresh':>8}{'bets':>7}{'win%':>7}{'roi%':>8}{'CLV':>7}")
        for frac in (0.25, 0.5, 1.0, 1.5):     # threshold scaled to the sport's move SD
            K = frac * move_sd
            over = diff > K; under = diff < -K; nb = int(over.sum() + under.sum())
            if nb < 30:
                continue
            wins = int((yt[over] > ot[over]).sum() + (yt[under] < ot[under]).sum())
            pnl = wins * JUICE - (nb - wins)
            clv = np.concatenate([ct[over] - ot[over], ot[under] - ct[under]]).mean()
            flag = " <-- beats vig" if wins / nb > 0.524 else ""
            print(f"{K:>8}{nb:>7}{wins/nb*100:>7.1f}{pnl/nb*100:>8.2f}{clv:>7.2f}{flag}")
        print()


if __name__ == "__main__":
    main()
