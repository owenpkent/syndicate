"""Broad resolved-Polymarket ingest -> data/defi.duckdb (all categories).

The earlier backfill only pulled CRYPTO markets. To ask the real prediction-market
question — *is Polymarket well-calibrated across politics / sports / crypto / etc.,
and is there a favorite-longshot bias?* — pull the most-liquid RESOLVED binary
markets regardless of topic, label each by a keyword heuristic (no category field is
exposed), and store the settled outcome + the pre-resolution mid history.

    python research/defi/ingest_polymarket_resolved.py            # top 1500 by volume
    python research/defi/ingest_polymarket_resolved.py --top 3000
"""
from __future__ import annotations

import argparse
import json

from _common import DEFI_DB, connect_duckdb, get_logger, http

log = get_logger("ingest_polymarket_resolved")
GAMMA = "https://gamma-api.polymarket.com/markets"
PM_HIST = "https://clob.polymarket.com/prices-history"

CATS = {
    "Crypto": ("bitcoin", "btc", "ethereum", " eth", "solana", " sol", "crypto",
               "dogecoin", "xrp", "ripple", "$"),
    "Politics": ("election", "president", "senate", "congress", "trump", "biden",
                 "democrat", "republican", "governor", "poll", "prime minister",
                 "parliament", "nominee", "approval", "vote"),
    "Sports": ("nba", "nfl", "mlb", "nhl", "premier league", "uefa", "la liga",
               "atp", "wta", "ufc", " f1", "grand prix", " vs ", " vs.", "super bowl",
               "world cup", "champions league", "ncaa", "olympic"),
}


def categorize(q: str) -> str:
    ql = q.lower()
    for cat, kws in CATS.items():
        if any(k in ql for k in kws):
            return cat
    return "Other"


def resolved_markets(top: int):
    out, seen = [], set()
    for off in range(0, 6000, 100):
        b = http("GET", GAMMA, params={"limit": 100, "offset": off, "closed": "true",
                 "order": "volume24hr", "ascending": "false"}, log=log).json()
        for m in b:
            cid = m.get("conditionId") or m.get("condition_id")
            if not cid or cid in seen or not m.get("clobTokenIds"):
                continue
            try:
                outs = json.loads(m.get("outcomes") or "[]")
                prices = json.loads(m.get("outcomePrices") or "[]")
            except Exception:  # noqa: BLE001
                continue
            if len(outs) != 2 or len(prices) != 2:        # binary only
                continue
            seen.add(cid)
            out.append(m)
        if len(b) < 100 or len(out) >= top:
            break
    return out[:top]


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest resolved Polymarket markets (all categories)")
    p.add_argument("--top", type=int, default=1500)
    p.add_argument("--db", default=DEFI_DB)
    args = p.parse_args()

    con = connect_duckdb(args.db)
    con.execute("""CREATE TABLE IF NOT EXISTS pm_resolved (
        condition_id TEXT PRIMARY KEY, question TEXT, yes_outcome DOUBLE, end_date TIMESTAMP);""")
    if "category" not in {c[1] for c in con.execute("PRAGMA table_info(pm_resolved)").fetchall()}:
        con.execute("ALTER TABLE pm_resolved ADD COLUMN category TEXT")
    con.execute("""CREATE TABLE IF NOT EXISTS pm_price_hist (
        token_id TEXT, condition_id TEXT, question TEXT, outcome TEXT,
        t TIMESTAMP, mid DOUBLE, PRIMARY KEY (token_id, t));""")

    markets = resolved_markets(args.top)
    from datetime import datetime
    ph, res = 0, 0
    for m in markets:
        cid = m.get("conditionId") or m.get("condition_id")
        q = (m.get("question") or "")[:200]
        outs = json.loads(m["outcomes"]); toks = json.loads(m["clobTokenIds"])
        prices = json.loads(m["outcomePrices"])
        yi = next((i for i, o in enumerate(outs) if str(o).lower() == "yes"), 0)
        yes = float(prices[yi]) if prices else None
        end = m.get("endDate") or m.get("end_date_iso")
        end_ts = None
        if end:
            try:
                end_ts = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:  # noqa: BLE001
                end_ts = None
        con.execute("""INSERT INTO pm_resolved VALUES (?,?,?,?,?)
            ON CONFLICT (condition_id) DO UPDATE SET yes_outcome=excluded.yes_outcome,
            end_date=excluded.end_date, category=excluded.category;""",
            [cid, q, yes, end_ts, categorize(q)])
        res += 1
        # price history for the Yes-side token (the prediction we score)
        tok = toks[yi] if yi < len(toks) else toks[0]
        try:
            hist = http("GET", PM_HIST, params={"market": tok, "interval": "max",
                        "fidelity": 60}, log=log).json().get("history", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("hist %s failed: %s", tok[:10], exc); continue
        if hist:
            con.executemany("INSERT INTO pm_price_hist VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING;",
                [(tok, cid, q, outs[yi], datetime.utcfromtimestamp(pt["t"]), float(pt["p"])) for pt in hist])
            ph += len(hist)
        if res % 250 == 0:
            log.info("  %d/%d markets, %d hist points", res, len(markets), ph)

    by_cat = con.execute("SELECT category, count(*) FROM pm_resolved GROUP BY 1 ORDER BY 2 DESC").fetchall()
    con.close()
    log.info("done. %d resolved markets, +%d hist points. by category: %s", res, ph, dict(by_cat))


if __name__ == "__main__":
    main()
