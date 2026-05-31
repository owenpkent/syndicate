"""Closing Line Value (CLV) — did we beat the closing price?

CLV is the sharpest single proxy for a betting edge: consistently getting better
odds than the market's closing line predicts long-run profit even before results
are known. ``CLV = executed_odds / closing_odds - 1`` per trade.
"""
from __future__ import annotations

import numpy as np

from ..config import load_settings
from ..db import Database
from ..store import HOME, Store


def analyze(store: Store) -> dict | None:
    rows = store.clv_rows()
    if not rows:
        return None
    improvements = []
    for executed_odds, side, home_close, away_close in rows:
        closing = float(home_close if side == HOME else away_close)
        if closing > 0:
            improvements.append(float(executed_odds) / closing - 1)
    arr = np.array(improvements)
    return {
        "n": len(arr),
        "avg_clv": float(arr.mean()),
        "beat_rate": float((arr > 0).mean() * 100),
    }


def main() -> None:
    print("--- Closing Line Value (CLV) Analysis ---")
    result = analyze(Store(Database(load_settings().db)))
    if not result:
        print("No matched trades with closing odds. Run 'make demo' or backfill first.")
        return
    print(f"Trades analyzed:    {result['n']}")
    print(f"Average CLV edge:   {result['avg_clv'] * 100:+.2f}%")
    print(f"Beat closing line:  {result['beat_rate']:.2f}% of the time")
    if result["avg_clv"] > 0.02:
        print("\nSTATUS: ALPHA-POSITIVE (beating the market by >2%)")
    elif result["avg_clv"] > 0:
        print("\nSTATUS: MARGINAL (beating the market slightly)")
    else:
        print("\nSTATUS: SUB-PAR (lagging closing-line movement)")


if __name__ == "__main__":
    main()
