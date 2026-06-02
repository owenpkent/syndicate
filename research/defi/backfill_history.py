"""One-shot historical backfill for the DeFi stores -> data/defi.duckdb.

The live collectors only see the present; this seeds weeks of history so the
notebooks (lead-lag, basis, Polymarket calibration) have real numbers now
instead of waiting on the cron. All sources free, no keys.

What backfills (and what does NOT):
  hl_candles        Hyperliquid 1m OHLCV (price/mark proxy) per coin
  hl_funding_hist   Hyperliquid hourly funding + premium per coin
  cex_candles       Coinbase spot OHLCV (paginated)
  pm_price_hist     Polymarket per-market mid time series (open + resolved crypto)
  pm_resolved       Resolved crypto markets WITH outcome (outcomePrices) -> calibration
  -- NOT order-book depth: hl_book / pm_book spreads are live-snapshot only.

    python research/defi/backfill_history.py                 # 14d, default coins
    python research/defi/backfill_history.py --days 30 --coins BTC,ETH,SOL
    python research/defi/backfill_history.py --only polymarket
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from _common import DEFI_DB, connect_duckdb, get_logger, http

log = get_logger("backfill_history")
HL = "https://api.hyperliquid.xyz/info"
CB = "https://api.exchange.coinbase.com/products/{p}/candles"
GAMMA = "https://gamma-api.polymarket.com/markets"
PM_HIST = "https://clob.polymarket.com/prices-history"

DEFAULT_COINS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE"]
CB_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
               "XRP": "XRP-USD", "DOGE": "DOGE-USD"}
CRYPTO_KW = ("bitcoin", "btc", "ethereum", " eth", "solana", " sol", "crypto",
             "dogecoin", "xrp", "ripple")


def _ts(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- Hyperliquid
def hl_candles(coin: str, interval: str, start_ms: int, end_ms: int):
    out, cur = [], start_ms
    while cur < end_ms:
        batch = http("POST", HL, json={"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval, "startTime": cur, "endTime": end_ms}},
            log=log).json()
        if not batch:
            break
        out += batch
        last = batch[-1]["t"]
        if last <= cur or len(batch) < 5000:
            break
        cur = last + 1
    return out


def hl_funding(coin: str, start_ms: int):
    out, cur = [], start_ms
    while True:
        batch = http("POST", HL, json={"type": "fundingHistory", "coin": coin,
                     "startTime": cur}, log=log).json()
        if not batch:
            break
        out += batch
        last = batch[-1]["time"]
        if last <= cur or len(batch) < 500:
            break
        cur = last + 1
    return out


# ------------------------------------------------------------------- Coinbase
def cb_candles(product: str, gran: int, start_s: int, end_s: int):
    out, s = [], start_s
    span = gran * 300
    while s < end_s:
        e = min(s + span, end_s)
        rows = http("GET", CB.format(p=product), params={
            "granularity": gran,
            "start": datetime.fromtimestamp(s, timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(e, timezone.utc).isoformat()}, log=log).json()
        out += rows
        s = e
        time.sleep(0.25)  # be polite to the public endpoint
    return out


# ----------------------------------------------------------------- Polymarket
def crypto_markets(closed: bool, pages: int = 5):
    out, seen = [], set()
    for off in range(0, pages * 100, 100):
        b = http("GET", GAMMA, params={"limit": 100, "offset": off,
                 "closed": "true" if closed else "false",
                 "order": "volume24hr", "ascending": "false"}, log=log).json()
        for m in b:
            cid = m.get("conditionId") or m.get("condition_id")
            q = (m.get("question") or "").lower()
            if cid and cid not in seen and any(k in q for k in CRYPTO_KW) and m.get("clobTokenIds"):
                seen.add(cid)
                out.append(m)
        if len(b) < 100:
            break
    return out


def pm_history(token_id: str):
    h = http("GET", PM_HIST, params={"market": token_id, "interval": "max",
             "fidelity": 60}, log=log).json()
    return h.get("history", [])


# ---------------------------------------------------------------------- store
def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS hl_candles (
        coin TEXT, interval TEXT, t TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE,
        close DOUBLE, volume DOUBLE, trades INTEGER, PRIMARY KEY (coin, interval, t));""")
    con.execute("""CREATE TABLE IF NOT EXISTS hl_funding_hist (
        coin TEXT, t TIMESTAMP, funding_rate DOUBLE, premium DOUBLE,
        PRIMARY KEY (coin, t));""")
    con.execute("""CREATE TABLE IF NOT EXISTS cex_candles (
        venue TEXT, asset TEXT, granularity_s INTEGER, t TIMESTAMP, open DOUBLE,
        high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
        PRIMARY KEY (venue, asset, granularity_s, t));""")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_price_hist (
        token_id TEXT, condition_id TEXT, question TEXT, outcome TEXT,
        t TIMESTAMP, mid DOUBLE, PRIMARY KEY (token_id, t));""")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_resolved (
        condition_id TEXT PRIMARY KEY, question TEXT, yes_outcome DOUBLE,
        end_date TIMESTAMP);""")


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill DeFi history -> data/defi.duckdb")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--coins", default=",".join(DEFAULT_COINS))
    p.add_argument("--cb-granularity", type=int, default=60)
    p.add_argument("--only", choices=("hl", "cex", "polymarket"), help="run one source")
    p.add_argument("--db", default=DEFI_DB)
    args = p.parse_args()
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 86400 * 1000
    con = connect_duckdb(args.db)
    ensure_tables(con)

    if args.only in (None, "hl"):
        for c in coins:
            cd = hl_candles(c, "1m", start_ms, now_ms)
            con.executemany("INSERT INTO hl_candles VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;",
                [(c, "1m", _ts(k["t"]), float(k["o"]), float(k["h"]), float(k["l"]),
                  float(k["c"]), float(k["v"]), int(k.get("n") or 0)) for k in cd])
            fh = hl_funding(c, start_ms)
            con.executemany("INSERT INTO hl_funding_hist VALUES (?,?,?,?) ON CONFLICT DO NOTHING;",
                [(c, _ts(f["time"]), float(f["fundingRate"]), float(f.get("premium") or 0)) for f in fh])
            log.info("HL %-5s: +%d 1m candles, +%d funding points", c, len(cd), len(fh))

    if args.only in (None, "cex"):
        for c in coins:
            prod = CB_PRODUCTS.get(c)
            if not prod:
                continue
            rows = cb_candles(prod, args.cb_granularity, start_ms // 1000, now_ms // 1000)
            con.executemany("INSERT INTO cex_candles VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING;",
                [("coinbase", c, args.cb_granularity, _ts(r[0] * 1000),
                  float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])) for r in rows])
            log.info("Coinbase %-5s: +%d %ds candles", c, len(rows), args.cb_granularity)

    if args.only in (None, "polymarket"):
        markets = crypto_markets(closed=True) + crypto_markets(closed=False)
        ph_rows, res_rows = 0, 0
        for m in markets:
            cid = m.get("conditionId") or m.get("condition_id")
            q = (m.get("question") or "")[:160]
            try:
                toks = json.loads(m["clobTokenIds"])
                outs = json.loads(m.get("outcomes") or "[]")
            except Exception:  # noqa: BLE001
                continue
            if m.get("closed"):
                try:
                    prices = json.loads(m.get("outcomePrices") or "[]")
                    # Align to the 'Yes' outcome so the notebook's outcome='Yes'
                    # filter matches; fall back to the first outcome.
                    yi = next((i for i, o in enumerate(outs) if str(o).lower() == "yes"), 0)
                    yes = float(prices[yi]) if prices else None
                except Exception:  # noqa: BLE001
                    yes = None
                end = m.get("endDate") or m.get("end_date_iso")
                end_ts = None
                if end:
                    try:
                        end_ts = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:  # noqa: BLE001
                        end_ts = None
                con.execute(
                    "INSERT INTO pm_resolved (condition_id, question, yes_outcome, end_date) "
                    "VALUES (?,?,?,?) ON CONFLICT DO NOTHING;",
                    [cid, q, yes, end_ts])
                res_rows += 1
            for i, tok in enumerate(toks):
                outcome = outs[i] if i < len(outs) else str(i)
                try:
                    hist = pm_history(tok)
                except Exception as exc:  # noqa: BLE001
                    log.warning("pm hist %s failed: %s", tok[:10], exc); continue
                con.executemany("INSERT INTO pm_price_hist VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING;",
                    [(tok, cid, q, outcome, _ts(pt["t"] * 1000), float(pt["p"])) for pt in hist])
                ph_rows += len(hist)
        log.info("Polymarket: %d markets (%d resolved), +%d price-history points",
                 len(markets), res_rows, ph_rows)

    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("hl_candles", "hl_funding_hist", "cex_candles", "pm_price_hist", "pm_resolved")}
    con.close()
    log.info("done. totals: %s", counts)


if __name__ == "__main__":
    main()
