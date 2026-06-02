"""Cross-venue lead-lag: does Hyperliquid lead Coinbase (or vice-versa)?

The richer companion to analyze_leadlag.py. That script reads the sparse live
*snapshots* (cex_spot / hl_ctx, captured_at) -> only a few hours of grid points.
This one uses the accumulated **1-minute candles** (hl_candles vs cex_candles,
~3 days of overlap, ~5000 aligned minutes per major), which is enough to ask the
question two honest ways:

  1. CROSS-CORRELATION  corr(hl_ret[t], cex_ret[t+L]) over lags L=-K..K.
     L>0 => HL *leads* CEX by L minutes; L<0 => CEX leads HL; peak at L=0 =>
     they move together within the minute (the efficient-market default).

  2. PREDICTIVE TEST (the edge question). Walk-forward OOS: does the *other*
     venue's current return add predictive power for a venue's NEXT-minute
     return beyond its own lag? Incremental OOS R^2 ~ 0 => no minute-scale edge.

Honest caveat baked in: 1-minute bars cannot see sub-second lead-lag, which is
where genuine cross-venue HFT arb lives. A ~0 result here means "no edge at the
minute scale we can observe", not "venues are perfectly synced".

    python research/defi/leadlag_candles.py
    python research/defi/leadlag_candles.py --coins BTC,ETH,SOL --max-lag 5
"""
from __future__ import annotations

import argparse

import duckdb
import numpy as np
import pandas as pd

from _common import DEFI_DB, get_logger

log = get_logger("leadlag_candles")
MIN_OVERLAP = 500          # below this, correlations are noise


def load_aligned(con, coin: str) -> pd.DataFrame:
    """Inner-join HL and Coinbase 1m closes on the minute grid -> aligned returns."""
    df = con.execute(
        """
        WITH hl AS (
            SELECT date_trunc('minute', t) m, last(close ORDER BY t) px
            FROM hl_candles WHERE coin=? AND interval='1m' GROUP BY 1),
        cx AS (
            SELECT date_trunc('minute', t) m, last(close ORDER BY t) px
            FROM cex_candles WHERE venue='coinbase' AND asset=? GROUP BY 1)
        SELECT hl.m, hl.px AS hl_px, cx.px AS cx_px
        FROM hl JOIN cx USING (m) ORDER BY hl.m
        """,
        [coin, coin],
    ).df()
    if len(df) < MIN_OVERLAP:
        return pd.DataFrame()
    df["hl_r"] = np.log(df["hl_px"]).diff()
    df["cx_r"] = np.log(df["cx_px"]).diff()
    return df.dropna().reset_index(drop=True)


def crosscorr(hl_r, cx_r, max_lag):
    """corr(hl_ret[t], cex_ret[t+L]) for L=-max_lag..max_lag (L>0 = HL leads)."""
    h = (hl_r - hl_r.mean()) / (hl_r.std() + 1e-12)
    c = (cx_r - cx_r.mean()) / (cx_r.std() + 1e-12)
    n = len(h)
    out = {}
    for L in range(-max_lag, max_lag + 1):
        if L >= 0:
            a, b = h[:n - L], c[L:]
        else:
            a, b = h[-L:], c[:n + L]
        out[L] = float(np.mean(a * b)) if len(a) > 1 else float("nan")
    return out


def oos_increment(target_next, own, other, split=0.7):
    """OOS R^2 of predicting target_next from [own] vs [own, other].

    Chronological train/test. Returns (r2_own, r2_both, increment). A positive
    increment means the *other* venue's current return helps predict this
    venue's next minute beyond its own autocorrelation.
    """
    y = target_next
    n = len(y)
    cut = int(split * n)
    ybar = y[:cut].mean()

    def r2(cols):
        X = np.column_stack([np.ones(n)] + cols)
        beta, *_ = np.linalg.lstsq(X[:cut], y[:cut], rcond=None)
        pred = X[cut:] @ beta
        ss_res = np.sum((y[cut:] - pred) ** 2)
        ss_tot = np.sum((y[cut:] - ybar) ** 2)
        return float(1 - ss_res / ss_tot)

    r_own = r2([own])
    r_both = r2([own, other])
    return r_own, r_both, r_both - r_own


def run(coins, max_lag):
    con = duckdb.connect(DEFI_DB, read_only=True)
    rows = []
    try:
        for coin in coins:
            df = load_aligned(con, coin)
            if df.empty:
                log.info("%s: <%d aligned minutes, skipping", coin, MIN_OVERLAP)
                continue
            hl_r = df["hl_r"].to_numpy(); cx_r = df["cx_r"].to_numpy()
            cc = crosscorr(hl_r, cx_r, max_lag)
            peak_lag = max(cc, key=lambda k: abs(cc[k]))
            # predictive: next-minute CEX from own lag vs + HL lag
            cx_next = cx_r[1:]; cx_lag = cx_r[:-1]; hl_lag = hl_r[:-1]
            _, _, inc_cx = oos_increment(cx_next, cx_lag, hl_lag)
            # and the reverse: next-minute HL from own lag vs + CEX lag
            hl_next = hl_r[1:]
            _, _, inc_hl = oos_increment(hl_next, hl_r[:-1], cx_r[:-1])
            rows.append((coin, len(df), cc[0], peak_lag, cc[peak_lag], inc_cx, inc_hl))
    finally:
        con.close()

    if not rows:
        log.error("no coins had enough overlap"); return

    print("\n" + "=" * 80)
    print("HYPERLIQUID <-> COINBASE LEAD-LAG  (1m candles, overlapping window)")
    print("=" * 80)
    print(f"{'coin':5} {'mins':>6} {'corr@0':>8} {'peakLag':>8} {'corr@peak':>10} "
          f"{'dR2:CEX+HL':>11} {'dR2:HL+CEX':>11}")
    for coin, n, c0, pl, cp, icx, ihl in rows:
        print(f"{coin:5} {n:>6} {c0:>8.3f} {pl:>8d} {cp:>10.3f} "
              f"{icx:>11.4f} {ihl:>11.4f}")
    print()
    print("  corr@0     contemporaneous correlation (both move within the minute)")
    print("  peakLag    lag (min) maximising |corr|; >0 = HL leads, <0 = CEX leads, 0 = synced")
    print("  dR2:CEX+HL incremental OOS R^2 from adding HL's lag to predict next-min CEX")
    print("  dR2:HL+CEX  ... and CEX's lag to predict next-min HL")
    print("  ~0 increments => no exploitable lead-lag at the 1-minute scale (sub-second")
    print("  cross-venue arb is invisible to minute bars).")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coins", default="BTC,ETH,SOL,XRP")
    ap.add_argument("--max-lag", type=int, default=5, help="max lead-lag in minutes")
    a = ap.parse_args()
    run([x.strip().upper() for x in a.coins.split(",") if x.strip()], a.max_lag)


if __name__ == "__main__":
    main()
