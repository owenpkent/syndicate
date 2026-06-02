"""Snapshot Hyperliquid perps -> DuckDB (the core decentralized time-series gym).

Two tables, both append-only keyed by captured_at:

  hl_ctx   one row per coin per snapshot: funding, open interest, mark/oracle/mid
           price, premium, 24h volume. ONE info call covers all ~230 coins, so
           this doubles as the on-chain metrics panel (funding/OI across venue).
  hl_book  top-N L2 order-book levels (px, sz, n orders) per side for a majors
           subset — the microstructure series (depth, spread, imbalance).

Free, no key, no rate-limit pain at a few-minute cadence.

    python research/defi/capture_hyperliquid.py                 # majors book
    python research/defi/capture_hyperliquid.py --coins BTC,ETH,SOL,HYPE
"""
from __future__ import annotations

import argparse

from _common import DEFI_DB, connect_duckdb, get_logger, http, now_utc

log = get_logger("capture_hyperliquid")
INFO = "https://api.hyperliquid.xyz/info"
DEFAULT_COINS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE"]


def fetch_ctx():
    """[(coin, ctx_dict)] from metaAndAssetCtxs (meta.universe paired to ctxs)."""
    meta, ctxs = http("POST", INFO, json={"type": "metaAndAssetCtxs"}, log=log).json()
    return [(u["name"], c) for u, c in zip(meta["universe"], ctxs)]


def fetch_book(coin: str, depth: int):
    """[(side, level, px, sz, n)] for the top `depth` levels each side."""
    b = http("POST", INFO, json={"type": "l2Book", "coin": coin}, log=log).json()
    out = []
    for side, levels in zip(("bid", "ask"), b.get("levels", [[], []])):
        for i, lvl in enumerate(levels[:depth]):
            out.append((side, i, float(lvl["px"]), float(lvl["sz"]), int(lvl["n"])))
    return out


def _f(v):
    return float(v) if v not in (None, "") else None


def main() -> None:
    p = argparse.ArgumentParser(description="Snapshot Hyperliquid perps -> DuckDB")
    p.add_argument("--coins", default=",".join(DEFAULT_COINS),
                   help="comma list for L2 book capture (ctx always covers all coins)")
    p.add_argument("--depth", type=int, default=10, help="L2 levels per side")
    p.add_argument("--db", default=DEFI_DB)
    args = p.parse_args()
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    ts = now_utc()

    ctx = fetch_ctx()
    books = {}
    for c in coins:
        try:
            books[c] = fetch_book(c, args.depth)
        except Exception as exc:  # noqa: BLE001
            log.warning("l2Book %s failed: %s", c, exc)

    con = connect_duckdb(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS hl_ctx (
        captured_at TIMESTAMP, coin TEXT, funding DOUBLE, open_interest DOUBLE,
        mark_px DOUBLE, oracle_px DOUBLE, mid_px DOUBLE, premium DOUBLE,
        prev_day_px DOUBLE, day_ntl_vlm DOUBLE,
        PRIMARY KEY (coin, captured_at));""")
    con.execute("""CREATE TABLE IF NOT EXISTS hl_book (
        captured_at TIMESTAMP, coin TEXT, side TEXT, level INTEGER,
        px DOUBLE, sz DOUBLE, n_orders INTEGER,
        PRIMARY KEY (coin, side, level, captured_at));""")

    con.executemany(
        "INSERT INTO hl_ctx VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;",
        [(ts, coin, _f(c.get("funding")), _f(c.get("openInterest")),
          _f(c.get("markPx")), _f(c.get("oraclePx")), _f(c.get("midPx")),
          _f(c.get("premium")), _f(c.get("prevDayPx")), _f(c.get("dayNtlVlm")))
         for coin, c in ctx])
    book_rows = [(ts, coin, side, lvl, px, sz, n)
                 for coin, rows in books.items() for side, lvl, px, sz, n in rows]
    if book_rows:
        con.executemany(
            "INSERT INTO hl_book VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;", book_rows)
    tot_ctx = con.execute("SELECT count(*) FROM hl_ctx").fetchone()[0]
    con.close()
    log.info("hl_ctx +%d coins; hl_book +%d rows (%d coins, depth %d); %d total ctx rows.",
             len(ctx), len(book_rows), len(books), args.depth, tot_ctx)


if __name__ == "__main__":
    main()
