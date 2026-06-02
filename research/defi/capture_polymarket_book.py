"""Snapshot Polymarket CRYPTO market order books -> DuckDB pm_book.

The decentralized prediction-market leg of the DeFi pivot. Unlike the sports
divergence logger (research/log_polymarket_divergence.py, which uses Gamma mid
prices), this reads the real CLOB order book per outcome token for the most
liquid crypto markets (BTC/ETH/SOL price-threshold questions, etc.) and records
best bid/ask, mid, and depth over time — a microstructure series on a venue that
settles on-chain.

These markets ("Will BTC be above $X on <date>") are a clean prediction target:
the truth is a CEX/Hyperliquid price we already capture, so they can be scored.

    python research/defi/capture_polymarket_book.py
    python research/defi/capture_polymarket_book.py --top 40 --min-liq 5000
"""
from __future__ import annotations

import argparse
import json

from _common import DEFI_DB, connect_duckdb, get_logger, http, now_utc

log = get_logger("capture_polymarket_book")
GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_BOOK = "https://clob.polymarket.com/book"
KEYWORDS = ("bitcoin", "btc", "ethereum", " eth", "solana", " sol", "crypto",
            "dogecoin", "xrp", "ripple")


def liquid_crypto_markets(min_liq: float, top: int):
    """Most-liquid open crypto markets from Gamma, sorted by liquidity desc."""
    seen, out = set(), []
    for off in (0, 100, 200, 300, 400):
        b = http("GET", GAMMA, params={"limit": 100, "offset": off, "closed": "false",
                 "order": "volume24hr", "ascending": "false"}, log=log).json()
        for m in b:
            q = (m.get("question") or "").lower()
            cid = m.get("conditionId") or m.get("condition_id")
            if cid in seen or not any(k in q for k in KEYWORDS):
                continue
            if float(m.get("liquidityNum") or 0) < min_liq or not m.get("clobTokenIds"):
                continue
            seen.add(cid)
            out.append(m)
        if len(b) < 100:
            break
    out.sort(key=lambda m: float(m.get("liquidityNum") or 0), reverse=True)
    return out[:top]


def book_stats(token_id: str):
    """(best_bid, best_ask, mid, bid_depth, ask_depth) from the CLOB book."""
    bk = http("GET", CLOB_BOOK, params={"token_id": token_id}, log=log).json()
    bids, asks = bk.get("bids") or [], bk.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = max(float(b["price"]) for b in bids)
    best_ask = min(float(a["price"]) for a in asks)
    bid_depth = sum(float(b["size"]) for b in bids)
    ask_depth = sum(float(a["size"]) for a in asks)
    return best_bid, best_ask, (best_bid + best_ask) / 2, bid_depth, ask_depth


def main() -> None:
    p = argparse.ArgumentParser(description="Snapshot Polymarket crypto books -> DuckDB")
    p.add_argument("--top", type=int, default=30, help="most-liquid crypto markets to track")
    p.add_argument("--min-liq", type=float, default=2000.0)
    p.add_argument("--db", default=DEFI_DB)
    args = p.parse_args()
    ts = now_utc()

    markets = liquid_crypto_markets(args.min_liq, args.top)
    rows = []
    for m in markets:
        q = (m.get("question") or "")[:120]
        cid = m.get("conditionId") or m.get("condition_id")
        liq = float(m.get("liquidityNum") or 0)
        try:
            token_ids = json.loads(m["clobTokenIds"])
            outcomes = json.loads(m.get("outcomes") or "[]")
        except Exception:  # noqa: BLE001
            continue
        for i, tok in enumerate(token_ids):
            outcome = outcomes[i] if i < len(outcomes) else str(i)
            try:
                st = book_stats(tok)
            except Exception as exc:  # noqa: BLE001
                log.warning("book %s failed: %s", tok[:10], exc); continue
            if st:
                bb, ba, mid, bd, ad = st
                rows.append((ts, cid, q, tok, outcome, bb, ba, mid, bd, ad, liq))

    con = connect_duckdb(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS pm_book (
        captured_at TIMESTAMP, condition_id TEXT, question TEXT, token_id TEXT,
        outcome TEXT, best_bid DOUBLE, best_ask DOUBLE, mid DOUBLE,
        bid_depth DOUBLE, ask_depth DOUBLE, liquidity_num DOUBLE,
        PRIMARY KEY (token_id, captured_at));""")
    if rows:
        con.executemany(
            "INSERT INTO pm_book VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;", rows)
    total = con.execute("SELECT count(*) FROM pm_book").fetchone()[0]
    con.close()
    log.info("pm_book +%d token rows across %d crypto markets; %d total.",
             len(rows), len(markets), total)


if __name__ == "__main__":
    main()
