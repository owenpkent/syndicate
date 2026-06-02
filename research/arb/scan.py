"""Scan collected markets for arbitrage CANDIDATES — a monitor, not an executor.

Two riskless-by-construction checks (no event-matching, no model):

  sportsbook  cross-book: per game, best decimal price for each side across all
              books in `odds_snapshots`; if 1/best_A + 1/best_B < 1 you can back
              both sides for a guaranteed profit (the books disagree enough).
  polymarket  multi-outcome: within a mutually-exclusive **neg-risk** event (e.g.
              every candidate in an election), exactly one resolves YES. If the
              best-ask of every outcome sums to < 1, buy them all -> guaranteed $1.
              (A single binary Yes/No market can't arb: No is Yes's complement, so
              ask(Yes)+ask(No) >= 1 always.)

Honest by design: a `--buffer` filters out gaps smaller than realistic
slippage/fees, and most scans will report little or nothing — liquid venues are
bot-arbed in milliseconds, and a cron sees stale quotes. What survives is usually a
thin, hard-to-fill dislocation. Optional Slack alert via `SLACK_WEBHOOK_URL`.

    python research/arb/scan.py                       # both, 1% buffer
    python research/arb/scan.py --source sportsbook --buffer 0.0
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import duckdb
import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))
from sportsball.logging_conf import get_logger  # noqa: E402

log = get_logger("arb_scan")
SPORTS_DB = str(REPO / "data" / "sportsball.duckdb")
GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
CLOB_BOOK = "https://clob.polymarket.com/book"


def _clob_best_ask(token_id: str):
    """Live best ask (min ask price) + its size from the CLOB order book, or None."""
    try:
        bk = requests.get(CLOB_BOOK, params={"token_id": token_id},
                          headers={"User-Agent": "sb-arb/0.1"}, timeout=12).json()
    except Exception:  # noqa: BLE001
        return None
    asks = bk.get("asks") or []
    if not asks:
        return None
    best = min(asks, key=lambda a: float(a["price"]))
    return float(best["price"]), float(best["size"])


def scan_sportsbooks(buffer: float) -> list[dict]:
    """Cross-book h2h arbs from the latest odds_snapshots per game."""
    con = duckdb.connect(SPORTS_DB, read_only=True)
    rows = con.execute("""
        WITH latest AS (SELECT event_id, max(captured_at) m FROM odds_snapshots
                        WHERE market='h2h' GROUP BY 1),
        best AS (SELECT s.event_id, s.side, max(s.price) px,
                        arg_max(s.bookmaker, s.price) book
                 FROM odds_snapshots s JOIN latest l
                   ON s.event_id=l.event_id AND s.captured_at=l.m
                 WHERE s.market='h2h' AND s.price > 1 GROUP BY 1,2)
        SELECT event_id, list(side), list(px), list(book) FROM best GROUP BY 1
    """).fetchall()
    con.close()
    out = []
    for event_id, sides, pxs, books in rows:
        if len(sides) != 2:
            continue
        inv = 1.0 / pxs[0] + 1.0 / pxs[1]
        margin = 1.0 - inv
        if margin > buffer:
            out.append({"kind": "sportsbook", "event": event_id, "margin": margin,
                        "legs": [f"{s} @ {p:.2f} ({b})" for s, p, b in zip(sides, pxs, books)]})
    return out


def scan_polymarket(buffer: float, min_fill: float = 25.0, pages: int = 6) -> list[dict]:
    """Back-all arbs in mutually-exclusive (neg-risk) Polymarket events."""
    import json
    out = []
    for off in range(0, pages * 100, 100):
        try:
            evs = requests.get(GAMMA_EVENTS, params={"limit": 100, "offset": off,
                "closed": "false", "order": "volume24hr", "ascending": "false"},
                headers={"User-Agent": "sb-arb/0.1"}, timeout=20).json()
        except Exception as exc:  # noqa: BLE001
            log.warning("gamma events page %d failed: %s", off, exc); break
        for ev in evs:
            mks = ev.get("markets") or []
            # only true mutually-exclusive, multi-outcome groups
            negrisk = [m for m in mks if m.get("negRisk") and m.get("bestAsk") not in (None, "")]
            if len(negrisk) < 3:
                continue
            try:
                gamma_cost = sum(float(m["bestAsk"]) for m in negrisk)
            except Exception:  # noqa: BLE001
                continue
            if 1.0 - gamma_cost <= buffer:        # cheap Gamma pre-filter
                continue
            # Gamma's bestAsk is cached/stale — VERIFY against the live CLOB book,
            # which is the only fillable truth. Most candidates evaporate here.
            live, min_leg, ok = 0.0, float("inf"), True
            for m in negrisk:
                tok = (json.loads(m["clobTokenIds"]) or [None])[0] if m.get("clobTokenIds") else None
                ba = _clob_best_ask(tok) if tok else None
                if ba is None:
                    ok = False; break
                live += ba[0]; min_leg = min(min_leg, ba[1])
            if not ok:
                continue
            margin = 1.0 - live
            if margin > buffer and min_leg >= min_fill:   # fillable only
                out.append({"kind": "polymarket", "event": (ev.get("title") or "")[:60],
                            "margin": margin, "outcomes": len(negrisk),
                            "legs": [f"live sum(ask)={live:.3f} (gamma said {gamma_cost:.3f}); "
                                     f"min leg size {min_leg:.0f}"]})
        if len(evs) < 100:
            break
    return out


def alert_slack(cands: list[dict]) -> None:
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url or not cands:
        return
    lines = [f"*{len(cands)} arb candidate(s)*"] + [
        f"• [{c['kind']}] {c['event']} — margin {c['margin']*100:.2f}% — {'; '.join(c['legs'])}"
        for c in cands[:15]]
    try:
        requests.post(url, json={"text": "\n".join(lines)}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("slack post failed: %s", exc)


def main() -> None:
    p = argparse.ArgumentParser(description="Scan for arbitrage candidates (monitor only)")
    p.add_argument("--source", choices=("both", "sportsbook", "polymarket"), default="both")
    p.add_argument("--buffer", type=float, default=0.01,
                   help="min margin to flag (filters slippage/fees noise); 0.01 = 1%%")
    p.add_argument("--min-size", type=float, default=25.0,
                   help="min fillable size (shares) on the thinnest Polymarket leg")
    p.add_argument("--slack", action="store_true", help="post candidates to SLACK_WEBHOOK_URL")
    args = p.parse_args()

    cands = []
    if args.source in ("both", "sportsbook"):
        cands += scan_sportsbooks(args.buffer)
    if args.source in ("both", "polymarket"):
        cands += scan_polymarket(args.buffer, args.min_size)
    cands.sort(key=lambda c: -c["margin"])

    if not cands:
        log.info("no arb candidates above %.1f%% buffer (expected — liquid markets are efficient).",
                 args.buffer * 100)
    for c in cands:
        log.info("ARB %-10s margin %+.2f%% | %s | %s",
                 c["kind"], c["margin"] * 100, c["event"], "; ".join(c["legs"]))
    if args.slack:
        alert_slack(cands)


if __name__ == "__main__":
    main()
