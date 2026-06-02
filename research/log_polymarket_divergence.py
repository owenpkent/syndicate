"""Log pre-game Polymarket-vs-sharp divergence over time (free, no API credits).

The accumulating experiment for the prediction-market path: every run, compute
each pre-game MLB market's Polymarket prob vs the SHARP sportsbook no-vig prob, and
append the gap to DuckDB `polymarket_divergence`. Later we join to actual results
and ask: when Polymarket diverges from the sharp book by >X%, does buying the cheap
side win? (the real +EV test).

Free: the sharp line comes from `odds_snapshots` (already captured every 2h by
capture_snapshot.py — no extra Odds API credits); Polymarket from the Gamma API
(no key). Pre-game filter uses each game's commence_time (excludes in-progress
games, which contaminated the one-shot scan).

    python research/log_polymarket_divergence.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import median

import duckdb
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from backfill_odds_markets_duckdb import DEFAULT_DB  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"


def sharp_pregame(con, now):
    """{home_lower/away_lower: (no_vig_prob, event_id, commence)} from the latest
    odds_snapshots h2h per game, only games that haven't started."""
    rows = con.execute("""
        WITH latest AS (
            SELECT event_id, max(captured_at) mx FROM odds_snapshots
            WHERE sport='mlb' AND market='h2h' GROUP BY event_id)
        SELECT s.event_id, s.bookmaker, s.side, s.price, s.commence_time
        FROM odds_snapshots s JOIN latest l
          ON s.event_id=l.event_id AND s.captured_at=l.mx
        WHERE s.market='h2h' AND s.price>1
    """).fetchall()
    by_game = {}
    for eid, bk, side, price, commence in rows:
        g = by_game.setdefault(eid, {"sides": {}, "commence": commence})
        g["sides"].setdefault(side, []).append(1 / float(price))
    out = {}
    for eid, g in by_game.items():
        if g["commence"] is None or g["commence"] <= now:      # pre-game only
            continue
        s = g["sides"]
        if len(s) != 2:
            continue
        (n1, p1), (n2, p2) = [(k, median(v)) for k, v in s.items()]
        tot = p1 + p2
        out[n1.lower()] = (p1 / tot, eid, g["commence"])
        out[n2.lower()] = (p2 / tot, eid, g["commence"])
    return out


def fetch_poly():
    out = []
    for off in (0, 100, 200):
        b = requests.get(GAMMA, params={"limit": 100, "offset": off, "closed": "false",
                                        "order": "volume24hr", "ascending": "false"}, timeout=15).json()
        out += b
        if len(b) < 100:
            break
    return out


def main():
    now = datetime.utcnow()  # naive UTC to match DuckDB timestamps
    con = duckdb.connect(str(DEFAULT_DB))
    con.execute("""CREATE TABLE IF NOT EXISTS polymarket_divergence (
        captured_at TIMESTAMP, event_id TEXT, sport TEXT, side TEXT,
        poly_prob DOUBLE, sharp_prob DOUBLE, divergence DOUBLE,
        commence_time TIMESTAMP, liquidity DOUBLE, question TEXT,
        PRIMARY KEY (event_id, side, captured_at));""")
    sharp = sharp_pregame(con, now)
    if not sharp:
        print("no pre-game MLB sharp lines in odds_snapshots yet — nothing to log."); con.close(); return

    def match(name):
        pn = name.lower().strip()
        for full, v in sharp.items():
            if pn and (pn in full or full.split()[-1] == pn.split()[-1]):
                return v
        return None

    rows = []
    for m in fetch_poly():
        q = m.get("question", "")
        if " vs" not in q.lower():
            continue
        try:
            outs = json.loads(m.get("outcomes", "[]"))
            prices = [float(x) for x in json.loads(m.get("outcomePrices", "[]"))]
        except Exception:
            continue
        if len(outs) != 2 or len(prices) != 2 or max(prices) > 0.97 or min(prices) < 0.03:
            continue
        liq = float(m.get("liquidityNum", 0) or 0)
        for name, pp in zip(outs, prices):
            v = match(name)
            if v and liq > 500:
                sp, eid, commence = v
                rows.append((now, eid, "mlb", name, pp, sp, pp - sp, commence, liq, q[:80]))

    n = con.executemany(
        "INSERT INTO polymarket_divergence VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (event_id, side, captured_at) DO NOTHING;", rows) if rows else None
    total = con.execute("SELECT count(*) FROM polymarket_divergence").fetchone()[0]
    big = sum(1 for r in rows if abs(r[6]) > 0.03)
    con.close()
    print(f"logged {len(rows)} pre-game divergence rows ({big} with |gap|>3%); "
          f"{total} total observations in polymarket_divergence.")


if __name__ == "__main__":
    main()
