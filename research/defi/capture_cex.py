"""Snapshot centralized-exchange spot -> DuckDB cex_spot (ground truth + lead-lag).

Two roles:
  - ground truth: CEX spot is the reference price to settle/score on-chain
    predictions against.
  - lead-lag: captured densely alongside Hyperliquid mark price (capture_hyperliquid),
    a time-join answers "which venue moves first, and by how long" — the cleanest
    microstructure edge, transferred from the sports book lead-lag idea.

Coinbase + Kraken (Binance is HTTP 451 / geo-blocked here). The canonical `asset`
column (BTC/ETH/...) matches hl_ctx.coin so the lead-lag join is trivial.

    python research/defi/capture_cex.py
    python research/defi/capture_cex.py --assets BTC,ETH,SOL
"""
from __future__ import annotations

import argparse

from _common import DEFI_DB, connect_duckdb, get_logger, http, now_utc

log = get_logger("capture_cex")

# canonical asset -> (coinbase product, kraken pair)
SYMBOLS = {
    "BTC": ("BTC-USD", "XBTUSD"), "ETH": ("ETH-USD", "ETHUSD"),
    "SOL": ("SOL-USD", "SOLUSD"), "XRP": ("XRP-USD", "XRPUSD"),
    "DOGE": ("DOGE-USD", "XDGUSD"),
}
COINBASE = "https://api.exchange.coinbase.com/products/{p}/ticker"
KRAKEN = "https://api.kraken.com/0/public/Ticker"


def coinbase(asset: str, product: str):
    d = http("GET", COINBASE.format(p=product), log=log).json()
    return ("coinbase", asset, product,
            float(d["bid"]), float(d["ask"]), float(d["price"]), float(d.get("volume") or 0))


def kraken(asset: str, pair: str):
    d = http("GET", KRAKEN, params={"pair": pair}, log=log).json()
    r = list(d["result"].values())[0]   # single pair -> single result value
    return ("kraken", asset, pair,
            float(r["b"][0]), float(r["a"][0]), float(r["c"][0]), float(r["v"][1]))


def main() -> None:
    p = argparse.ArgumentParser(description="Snapshot CEX spot -> DuckDB cex_spot")
    p.add_argument("--assets", default=",".join(SYMBOLS),
                   help="comma list of canonical assets (subset of %s)" % ",".join(SYMBOLS))
    p.add_argument("--db", default=DEFI_DB)
    args = p.parse_args()
    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    ts = now_utc()

    rows = []
    for a in assets:
        if a not in SYMBOLS:
            log.warning("unknown asset %s — skipping", a); continue
        cb_product, kr_pair = SYMBOLS[a]
        for fn, arg in ((coinbase, cb_product), (kraken, kr_pair)):
            try:
                venue, asset, sym, bid, ask, last, vol = fn(a, arg)
                rows.append((ts, venue, asset, sym, bid, ask, last, vol))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s %s failed: %s", fn.__name__, a, exc)

    con = connect_duckdb(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS cex_spot (
        captured_at TIMESTAMP, venue TEXT, asset TEXT, symbol TEXT,
        bid DOUBLE, ask DOUBLE, last DOUBLE, volume DOUBLE,
        PRIMARY KEY (venue, asset, captured_at));""")
    if rows:
        con.executemany(
            "INSERT INTO cex_spot VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;", rows)
    total = con.execute("SELECT count(*) FROM cex_spot").fetchone()[0]
    con.close()
    log.info("cex_spot +%d rows (%d assets x venues); %d total.",
             len(rows), len(assets), total)


if __name__ == "__main__":
    main()
