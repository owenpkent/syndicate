"""Ingest real closing odds into ``events.home_close`` / ``away_close``.

This is the one hard blocker from the roadmap's Tier 1: the free NBA data has
scores but **no lines**, so every "does it make money" question is currently
bracketed between a naive and an efficient market instead of priced against the
real one. The ``events`` columns already exist; this populates them, which is
exactly what makes ``make clv`` (and a real, vs. synthetic-bracket, odds
backtest) light up.

Two sources, both reduced to ``(canonical_event_id, home_close, away_close)`` in
**pure, unit-tested** parsers so the networked path is a thin wrapper:

* **Offline file** (``--file feed.json``): a list of records carrying
  ``sport, date, home_team, away_team, home_close, away_close`` (decimal odds).
  Lets a historical closing-odds dataset (CSV→JSON) be loaded with no key — the
  recommended way to backfill a real CLV history.
* **The Odds API** (``ODDS_API_KEY`` set): the ``/v4/sports/{sport}/odds`` h2h
  market; we take the **median** decimal price per team across books as a robust
  consensus line. (For a true *closing* line, snapshot near tip-off.)

Closing odds only ever *decorate an existing game* (UPDATE, never a stub insert),
so a line for a game we haven't ingested is simply skipped.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..matching import canonical_event_id
from ..quant.odds import implied_prob
from ..store import Store

log = get_logger("ingest_odds")

ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
SPORT_KEYS = {"nba": "basketball_nba", "nfl": "americanfootball_nfl",
              "mlb": "baseball_mlb", "nhl": "icehockey_nhl"}

# A real two-sided book quote's implied probabilities sum to a little over 1.0 —
# the vig / overround. Typical NBA closing vig is ~1.02 (sharp) to ~1.10 (heavy
# juice); we reject anything outside a slightly wider band so a mis-paired,
# stale, or typo'd line never poisons training. The lower bound stays at 1.01
# (not 1.02) so genuinely sharp / reduced-juice lines — e.g. a -105/-105 book at
# ~1.025, or a sharp consensus near 1.017 — aren't false-dropped, while the real
# failure mode (two-favorites mis-pair, a longshot typo) lands well outside it.
# Provenance: one bad quote flipped a published backtest from +29% to -6% ROI
# (arXiv 2306.01740), so this guard is load-bearing, not cosmetic.
VIG_MIN, VIG_MAX = 1.01, 1.12


# -- pure core (unit-testable, no I/O) --------------------------------------
def passes_vig_guard(home_close: float, away_close: float) -> bool:
    """True if two decimal prices imply a plausible bookmaker vig.

    Rejects quotes whose two-sided implied probabilities sum outside
    ``[VIG_MIN, VIG_MAX]`` — a sum well below 1 means the sides were mis-paired
    or one is stale; well above means a typo / off-board quote. Either way the
    line is not trustworthy enough to persist as a training target.
    """
    overround = implied_prob(home_close) + implied_prob(away_close)
    return VIG_MIN <= overround <= VIG_MAX


def _to_decimal(price) -> float | None:
    """Best-effort decimal odds from a price that may be decimal or American.

    Decimal odds are > 1.0 and < 100; American are >= +100 or negative. Returns
    ``None`` for anything non-positive/garbage so a bad quote is skipped.
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    if p < 0:                       # American underdog/favorite (negative)
        return round(1 + 100 / abs(p), 4)
    if p >= 100:                    # American positive
        return round(1 + p / 100, 4)
    if p > 1.0:                     # already decimal
        return round(p, 4)
    return None


def parse_file_feed(records: list[dict]) -> list[tuple]:
    """``[(event_id, home_close, away_close)]`` from generic feed records.

    Each record needs ``sport``, ``date``, home/away team names (``home_team`` /
    ``home`` either spelling) and ``home_close`` / ``away_close`` (decimal or
    American). Records missing a usable pair are skipped.
    """
    out: list[tuple] = []
    for r in records:
        home = r.get("home_team") or r.get("home")
        away = r.get("away_team") or r.get("away")
        sport = r.get("sport", "nba")
        when = r.get("date") or r.get("commence_time")
        if not (home and away and when):
            continue
        hc = _to_decimal(r.get("home_close"))
        ac = _to_decimal(r.get("away_close"))
        if hc is None or ac is None or not passes_vig_guard(hc, ac):
            continue
        out.append((canonical_event_id(sport, when, away, home), hc, ac))
    return out


def parse_odds_api(raw: list[dict], sport: str = "nba") -> list[tuple]:
    """``[(event_id, home_close, away_close)]`` from The Odds API h2h response.

    Takes the **median** decimal price per team across all bookmakers' ``h2h``
    markets as a robust consensus line. Events without both teams priced are
    skipped.
    """
    out: list[tuple] = []
    for ev in raw:
        home, away = ev.get("home_team"), ev.get("away_team")
        when = ev.get("commence_time")
        if not (home and away and when):
            continue
        prices: dict[str, list[float]] = {home: [], away: []}
        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for oc in market.get("outcomes", []):
                    dec = _to_decimal(oc.get("price"))
                    if dec is not None and oc.get("name") in prices:
                        prices[oc["name"]].append(dec)
        if not prices[home] or not prices[away]:
            continue
        hc, ac = round(median(prices[home]), 4), round(median(prices[away]), 4)
        if not passes_vig_guard(hc, ac):
            continue
        out.append((canonical_event_id(sport, when, away, home), hc, ac))
    return out


# -- I/O wrappers -----------------------------------------------------------
def fetch_odds_api(api_key: str, sport: str = "nba") -> list[dict]:
    """Fetch current h2h odds from The Odds API. Returns [] on any error."""
    import requests
    sport_key = SPORT_KEYS.get(sport, sport)
    try:
        resp = requests.get(
            ODDS_API_URL.format(sport_key=sport_key),
            params={"apiKey": api_key, "regions": "us", "markets": "h2h",
                    "oddsFormat": "decimal"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        log.error("The Odds API fetch failed: %s", exc)
        return []


def apply_closing_odds(store: Store, rows: list[tuple]) -> int:
    """UPDATE each (event_id, home_close, away_close); returns count attempted."""
    for event_id, home_close, away_close in rows:
        store.update_closing_odds(event_id, home_close, away_close)
    log.info("Applied closing odds for %d events.", len(rows))
    return len(rows)


def apply_closing_odds_duckdb(db_path: str, rows: list[tuple]) -> tuple[int, int]:
    """UPDATE ``events.home_close``/``away_close`` in a DuckDB file (offline path).

    The DuckDB research store carries the same ``events`` schema as Postgres, so
    closing lines can be attached without a server — handy for the local
    walk-forward eval. Like the Postgres path, lines only *decorate* games we
    already have, so a feed row whose ``event_id`` isn't in the DuckDB is skipped.
    Returns ``(matched, total)``.
    """
    import duckdb
    con = duckdb.connect(db_path, read_only=False)
    try:
        existing = {r[0] for r in con.execute("SELECT event_id FROM events").fetchall()}
        matched = 0
        con.execute("BEGIN TRANSACTION")
        for event_id, home_close, away_close in rows:
            if event_id not in existing:
                continue
            con.execute("UPDATE events SET home_close = ?, away_close = ? WHERE event_id = ?",
                        [float(home_close), float(away_close), event_id])
            matched += 1
        con.execute("COMMIT")
    finally:
        con.close()
    log.info("DuckDB: applied closing odds to %d of %d feed games.", matched, len(rows))
    return matched, len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest real closing odds -> events")
    parser.add_argument("--file", help="offline JSON feed (list of records)")
    parser.add_argument("--duckdb", help="write to this DuckDB file instead of Postgres "
                                         "(offline path; requires --file)")
    parser.add_argument("--sport", default="nba")
    args = parser.parse_args()

    settings = load_settings()
    if args.file:
        records = json.loads(Path(args.file).read_text())
        rows = parse_file_feed(records)
    elif settings.has_odds_api_key():
        rows = parse_odds_api(fetch_odds_api(settings.odds_api_key, args.sport), args.sport)
    else:
        log.error("No --file and no ODDS_API_KEY — nothing to ingest. "
                  "See docs/ROADMAP.md Tier 1 (closing-odds feed).")
        return

    if args.duckdb:
        apply_closing_odds_duckdb(args.duckdb, rows)
        return

    store = Store(Database(settings.db))
    store.db.connect()
    if not store.available:
        log.error("Database unavailable.")
        return
    apply_closing_odds(store, rows)


if __name__ == "__main__":
    main()
