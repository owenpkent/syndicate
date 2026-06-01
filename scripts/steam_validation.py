"""Validate the steam edge across sports: is line movement informative everywhere?

No model — just open total, close total, actual result. Bet the side the total
moved TOWARD, at the OPENING number, settle vs actual. If the close is sharper than
the open (it is), this is +EV. Runs on the free SBRO 10Y archives (nba/mlb/nhl/nfl,
each with open+close totals). Tests whether the NBA finding (+10-23% ROI) is a
sport-agnostic market-structure effect.

    python scripts/steam_validation.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"


def _f(x):
    try:
        v = float(x); return None if v != v else v
    except (TypeError, ValueError):
        return None


def load(sport: str):
    raw = json.load(open(DATA / f"{sport}_archive_10Y.json"))
    out = []
    for r in raw:
        ot, ct = _f(r.get("open_over_under")), _f(r.get("close_over_under"))
        hf = _f(r.get("home_final")) if r.get("home_final") is not None else _f(r.get("home_score"))
        af = _f(r.get("away_final")) if r.get("away_final") is not None else _f(r.get("away_score"))
        if None in (ot, ct, hf, af):
            continue
        out.append((ot, ct, hf + af))
    return out


def steam(games, minmove):
    n = w = 0; pnl = 0.0
    for ot, ct, tot in games:
        mv = ct - ot
        if abs(mv) < minmove or tot == ot:
            continue
        win = (tot > ot) if mv > 0 else (tot < ot)   # follow the move, bet at open
        n += 1; w += win; pnl += 0.91 if win else -1   # -110
    return n, (w / n * 100 if n else 0), (pnl / n * 100 if n else 0)


def main():
    # per-sport move thresholds scaled to the sport's total magnitude
    cfg = {"nba": (1, 2, 4), "mlb": (0.5, 1, 1.5), "nhl": (0.25, 0.5, 1), "nfl": (1, 2, 3)}
    for sport, thresholds in cfg.items():
        f = DATA / f"{sport}_archive_10Y.json"
        if not f.exists():
            print(f"{sport}: archive not found"); continue
        g = load(sport)
        tot_sd = np.std([x[2] for x in g])
        print(f"\n=== {sport.upper()} ({len(g)} games, total SD {tot_sd:.1f}) — follow the steam, bet at open ===")
        print(f"{'min move':>10}{'bets':>8}{'win%':>8}{'ROI%':>8}")
        for mm in thresholds:
            n, wr, roi = steam(g, mm)
            print(f"{mm:>10}{n:>8}{wr:>8.1f}{roi:>8.2f}")


if __name__ == "__main__":
    main()
