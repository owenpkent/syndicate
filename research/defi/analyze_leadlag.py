"""Cross-venue basis + lead-lag analysis over data/defi.duckdb.

Reads the accumulating snapshots (read-only) and asks, per asset:

  basis     how far Hyperliquid mark sits above/below CEX spot (mean/std/last),
            and the inter-CEX spread (Coinbase vs Kraken) as a sanity floor.
  lead-lag  on a common time grid, cross-correlate HL mark returns vs CEX last
            returns at lags +/-K steps. The lag maximizing correlation says who
            moves first: HL-leads (CEX follows) vs CEX-leads (HL follows).

Lead-lag needs accumulated history — with few points it reports basis only and
says how many more grid points are needed. Honest by construction (no look-ahead;
returns are contemporaneous, the lag is the whole question).

    python research/defi/analyze_leadlag.py
    python research/defi/analyze_leadlag.py --grid 5 --max-lag 6 --venue coinbase
"""
from __future__ import annotations

import argparse

import numpy as np

from _common import DEFI_DB, connect_duckdb, get_logger

log = get_logger("analyze_leadlag")
MIN_RETURNS = 20  # below this, lead-lag correlation is noise


def basis_report(con, venue: str):
    rows = con.execute("""
        WITH latest_hl AS (
            SELECT coin, last(mark_px ORDER BY captured_at) mark,
                   avg(mark_px) am, stddev_pop(mark_px) sm
            FROM hl_ctx GROUP BY coin),
        latest_cx AS (
            SELECT asset, venue, last(last ORDER BY captured_at) px
            FROM cex_spot GROUP BY asset, venue)
        SELECT h.coin, h.mark, c.px,
               h.mark - c.px AS basis_now, h.am, h.sm
        FROM latest_hl h JOIN latest_cx c ON c.asset = h.coin AND c.venue = ?
        ORDER BY h.coin
    """, [venue]).fetchall()
    return rows


def cex_spread(con):
    return con.execute("""
        WITH p AS (
            SELECT asset, venue, last(last ORDER BY captured_at) px
            FROM cex_spot GROUP BY asset, venue)
        SELECT asset,
               max(CASE WHEN venue='coinbase' THEN px END) cb,
               max(CASE WHEN venue='kraken'   THEN px END) kr
        FROM p GROUP BY asset ORDER BY asset
    """).fetchall()


def aligned_series(con, asset: str, grid_min: int, venue: str):
    """(hl_marks, cex_lasts) on a shared `grid_min`-minute grid, time-ordered."""
    rows = con.execute(f"""
        WITH hl AS (
            SELECT time_bucket(INTERVAL '{grid_min} minutes', captured_at) t,
                   avg(mark_px) v FROM hl_ctx WHERE coin = ? GROUP BY 1),
        cx AS (
            SELECT time_bucket(INTERVAL '{grid_min} minutes', captured_at) t,
                   avg(last) v FROM cex_spot WHERE asset = ? AND venue = ? GROUP BY 1)
        SELECT hl.v, cx.v FROM hl JOIN cx USING (t) ORDER BY hl.t
    """, [asset, asset, venue]).fetchall()
    hl = np.array([r[0] for r in rows], float)
    cx = np.array([r[1] for r in rows], float)
    return hl, cx


def lead_lag(hl: np.ndarray, cx: np.ndarray, max_lag: int):
    """argmax-correlation lag of HL-returns vs CEX-returns. Positive lag => HL
    leads (CEX follows by `lag` grid steps). Returns (best_lag, best_corr, table)."""
    rh = np.diff(np.log(hl))
    rc = np.diff(np.log(cx))
    n = len(rh)
    out = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = rh[: n - lag], rc[lag:]
        else:
            a, b = rh[-lag:], rc[: n + lag]
        if len(a) < MIN_RETURNS or np.std(a) == 0 or np.std(b) == 0:
            continue
        out.append((lag, float(np.corrcoef(a, b)[0, 1])))
    if not out:
        return None, None, out
    best = max(out, key=lambda x: x[1])
    return best[0], best[1], out


def main() -> None:
    p = argparse.ArgumentParser(description="Cross-venue basis + lead-lag over defi.duckdb")
    p.add_argument("--grid", type=int, default=5, help="grid bucket minutes")
    p.add_argument("--max-lag", type=int, default=6, help="max lag in grid steps")
    p.add_argument("--venue", default="coinbase", help="CEX venue for lead-lag")
    p.add_argument("--db", default=DEFI_DB)
    args = p.parse_args()
    con = connect_duckdb(args.db)

    print(f"\n=== Basis: Hyperliquid mark vs {args.venue} spot ===")
    print(f"{'asset':6} {'hl_mark':>12} {'cex':>12} {'basis_now':>11} {'mean':>11} {'std':>9}")
    for coin, mark, px, basis_now, am, sm in basis_report(con, args.venue):
        print(f"{coin:6} {mark:>12.4f} {px:>12.4f} {basis_now:>11.4f} "
              f"{(am - px):>11.4f} {sm or 0:>9.4f}")

    print(f"\n=== Inter-CEX spread (Coinbase vs Kraken, last) ===")
    for asset, cb, kr in cex_spread(con):
        d = (cb - kr) if (cb is not None and kr is not None) else None
        print(f"{asset:6} cb={cb} kr={kr} diff={d}")

    print(f"\n=== Lead-lag (grid {args.grid}m, +lag = HL leads) ===")
    assets = [r[0] for r in con.execute("SELECT DISTINCT coin FROM hl_ctx ORDER BY coin").fetchall()]
    any_done = False
    for a in assets:
        hl, cx = aligned_series(con, a, args.grid, args.venue)
        if len(hl) < MIN_RETURNS + 2:
            continue
        lag, corr, _ = lead_lag(hl, cx, args.max_lag)
        if lag is None:
            continue
        any_done = True
        who = "HL leads" if lag > 0 else ("CEX leads" if lag < 0 else "synchronous")
        print(f"{a:6} {len(hl):4} pts | best lag {lag:+d} ({lag*args.grid:+d}m) "
              f"corr {corr:+.3f} -> {who}")
    if not any_done:
        npts = con.execute("SELECT count(DISTINCT time_bucket(INTERVAL '%d minutes', captured_at)) "
                           "FROM hl_ctx" % args.grid).fetchone()[0]
        print(f"  insufficient history: {npts} grid points so far; need "
              f">= {MIN_RETURNS + 2}. Let the cron accumulate (~"
              f"{(MIN_RETURNS + 2) * args.grid} min of data).")
    con.close()
    print()


if __name__ == "__main__":
    main()
