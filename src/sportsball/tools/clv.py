"""Closing Line Value (CLV) — the primary edge metric.

CLV is the sharpest single proxy for a betting edge: consistently getting a better
price than the market's closing line predicts long-run profit *regardless of
short-term variance*, and it reaches significance in far fewer bets than realized
P&L (see [RESEARCH_NOTES](../../docs/RESEARCH_NOTES.md)). So it's the headline
"do we have edge?" number, ahead of ROI.

``CLV = taken_odds / closing_odds - 1`` per position. We report it two ways:

* **signal CLV** (primary) — over *every* evaluated signal on a graded game, even
  ones we didn't bet: the largest, fastest-converging sample.
* **trade CLV** — over executed paper fills only: what we'd actually have captured.
"""
from __future__ import annotations

import numpy as np

from ..config import load_settings
from ..db import Database
from ..store import HOME, Store


def clv(taken_odds, closing_odds):
    """Per-position CLV, or ``None`` when either price is missing/non-positive."""
    if taken_odds and closing_odds and float(taken_odds) > 0 and float(closing_odds) > 0:
        return float(taken_odds) / float(closing_odds) - 1.0
    return None


def summarize(rows) -> dict | None:
    """``{n, avg_clv, beat_rate}`` from ``(taken_odds, side, home_close, away_close)``
    rows; ``None`` when nothing is priceable. Pure — unit-tests on plain tuples."""
    vals = []
    for taken_odds, side, home_close, away_close in rows:
        c = clv(taken_odds, home_close if side == HOME else away_close)
        if c is not None:
            vals.append(c)
    if not vals:
        return None
    arr = np.array(vals)
    return {"n": len(arr), "avg_clv": float(arr.mean()), "beat_rate": float((arr > 0).mean())}


def verdict(avg_clv: float) -> str:
    """Research-grounded read on an average CLV (positive ≈ profitable long-run)."""
    if avg_clv > 0.02:
        return "ALPHA-POSITIVE (beating the close by >2% — strong edge signal)"
    if avg_clv > 0:
        return "MARGINAL (beating the close slightly — promising, need more samples)"
    return "SUB-PAR (not beating the closing line — no demonstrated edge)"


def analyze(store: Store) -> dict | None:
    """Both views: signal CLV (primary) and trade CLV. ``None`` if neither prices."""
    signal = summarize(store.signal_clv_rows())
    trade = summarize(store.clv_rows())
    if signal is None and trade is None:
        return None
    return {"signal": signal, "trade": trade}


def _print(label: str, s: dict | None) -> None:
    if not s:
        print(f"{label:<16} no priced rows")
        return
    print(f"{label:<16} n={s['n']:<6} avg CLV {s['avg_clv'] * 100:+.2f}%  "
          f"beat-rate {s['beat_rate'] * 100:.1f}%")


def main() -> None:
    print("--- Closing Line Value (CLV) — the primary edge metric ---")
    result = analyze(Store(Database(load_settings().db)))
    if not result:
        print("No rows with closing odds. Run `make ingest-odds` (or `make demo`) first.")
        return
    _print("signals (all)", result["signal"])
    _print("trades (filled)", result["trade"])
    primary = result["signal"] or result["trade"]
    print("-" * 56)
    print(f"VERDICT (CLV): {verdict(primary['avg_clv'])}")
    print("CLV beats P&L as an edge gate: lower variance, significant in ~tens of "
          "bets vs thousands. Gate the model on this before trusting backtest ROI.")


if __name__ == "__main__":
    main()
