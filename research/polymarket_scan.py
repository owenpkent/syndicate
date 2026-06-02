"""First look at Polymarket — characterize markets + cross-venue divergence.

Two questions:
1. What's on Polymarket, and how tradeable (vig/spread/liquidity)? Unlike a
   sportsbook, Polymarket is an order book: Yes+No ~ 1.00, ~1c spread, no vig.
2. Where it overlaps a SHARPER market (sportsbook MLB lines we can pull free),
   does it misprice? Polymarket prob vs the sportsbook no-vig prob = the edge.
   The sportsbook is sharper on sports, so a divergence is a candidate +EV bet on
   the cheap Polymarket side.

Free: Gamma API (no key) for Polymarket; the free Odds API (1 credit) for the
sharp MLB line. Read-only, no trades.

    python research/polymarket_scan.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sportsball.matching import normalize_team  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"
ODDS_MLB = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"


def _key():
    env = Path(__file__).resolve().parent.parent / ".env"
    return os.getenv("ODDS_API_KEY") or next(
        (l.split("=", 1)[1].strip() for l in open(env) if l.startswith("ODDS_API_KEY=")), "")


def fetch_poly(limit=500):
    """Order by 24h volume so the liquid, active markets (incl. daily game
    markets) surface — the default/active filter buries them under futures."""
    out = []
    for off in range(0, limit, 100):
        r = requests.get(GAMMA, params={"limit": 100, "offset": off, "closed": "false",
                                        "order": "volume24hr", "ascending": "false"}, timeout=15)
        r.raise_for_status()
        batch = r.json()
        out += batch
        if len(batch) < 100:
            break
    return out


def categorize(q):
    ql = q.lower()
    for cat, kws in {
        "crypto": ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "microstrategy"),
        "sports": (" vs.", " vs ", "win the", "world cup", "super bowl", "nba", "mlb", "playoff"),
        "politics": ("president", "election", "senate", "peace", "war", "deal", "fed", "rate"),
        "pop": ("album", "movie", "gta", "box office", "oscar", "spotify"),
    }.items():
        if any(k in ql for k in kws):
            return cat
    return "other"


def main():
    print("=== Polymarket characterization ===")
    mk = fetch_poly(500)
    print(f"{len(mk)} active markets")
    cats = Counter(categorize(m.get("question", "")) for m in mk)
    print("by category:", dict(cats))
    # vig / spread / liquidity on two-way markets
    import json
    vigs, spreads, liqs = [], [], []
    for m in mk:
        try:
            prices = [float(x) for x in json.loads(m.get("outcomePrices", "[]"))]
        except Exception:
            continue
        if len(prices) == 2 and all(p > 0 for p in prices):
            vigs.append(sum(prices) - 1.0)
            if m.get("spread") is not None:
                spreads.append(float(m["spread"]))
            liqs.append(float(m.get("liquidityNum", 0) or 0))
    import statistics as st
    if vigs:
        print(f"two-way markets: {len(vigs)} | median |Yes+No−1| = {st.median(abs(v) for v in vigs):.4f} "
              f"(≈ no vig) | median spread {st.median(spreads) if spreads else 0:.3f} | "
              f"median liquidity ${st.median(liqs):,.0f}")

    # --- cross-venue: Polymarket MLB vs the sharp sportsbook line ---
    print("\n=== Cross-venue divergence: Polymarket vs sharp MLB line ===")
    key = _key()
    if not key or "your_" in key:
        print("(no ODDS_API_KEY — skipping the sportsbook comparison)"); return
    r = requests.get(ODDS_MLB, params={"apiKey": key, "regions": "us", "markets": "h2h",
                                       "oddsFormat": "decimal"}, timeout=20)
    if r.status_code != 200:
        print("Odds API error", r.status_code); return
    # sharp no-vig prob per team, keyed by FULL lowercase name (cross-sport robust)
    from statistics import median
    sharp = {}
    games = r.json()
    for g in games:
        h, a = g.get("home_team"), g.get("away_team")
        ph, pa = [], []
        for bk in g.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                for oc in mkt.get("outcomes", []):
                    if oc.get("name") == h and oc.get("price"):
                        ph.append(1 / float(oc["price"]))
                    elif oc.get("name") == a and oc.get("price"):
                        pa.append(1 / float(oc["price"]))
        if ph and pa:
            mh, ma = median(ph), median(pa)
            sharp[h.lower()] = mh / (mh + ma)
            sharp[a.lower()] = ma / (mh + ma)
    print(f"sportsbook: {len(games)} MLB games, {len(sharp)} priced sides")

    def match(poly_name):
        pn = poly_name.lower().strip()
        for full, p in sharp.items():
            if pn and (pn in full or full.split()[-1] == pn.split()[-1]):
                return p
        return None

    rows = []
    for m in mk:
        q = m.get("question", "")
        if " vs" not in q.lower():
            continue
        try:
            outs = json.loads(m.get("outcomes", "[]"))
            prices = [float(x) for x in json.loads(m.get("outcomePrices", "[]"))]
        except Exception:
            continue
        if len(outs) != 2 or len(prices) != 2:
            continue
        # skip decided / in-progress games (a pre-game market isn't at 0/1)
        if max(prices) > 0.92 or min(prices) < 0.08:
            continue
        for name, pp in zip(outs, prices):
            sp = match(name)
            if sp is not None and float(m.get("liquidityNum", 0) or 0) > 500:
                rows.append((q[:34], name[:16], pp, sp, pp - sp))
    rows.sort(key=lambda x: -abs(x[4]))
    if not rows:
        print("(no overlapping MLB game markets matched right now)"); return
    print(f"{'market':<35}{'side':<17}{'poly':>6}{'sharp':>7}{'edge':>7}")
    for q, side, pp, sp, d in rows[:15]:
        flag = "  <-- poly cheap" if d < -0.03 else ("  <-- poly rich" if d > 0.03 else "")
        print(f"{q:<35}{side:<17}{pp:>6.2f}{sp:>7.2f}{d:>+7.2f}{flag}")
    print("\nedge = poly_prob − sharp_prob. Negative = Polymarket prices the side BELOW the\n"
          "sharp book → candidate +EV BUY on Polymarket (if the sportsbook is the sharper number).")


if __name__ == "__main__":
    main()
