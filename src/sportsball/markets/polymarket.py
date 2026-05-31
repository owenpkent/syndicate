"""Polymarket market discovery via the Gamma API.

Resolves *real* CLOB token ids (``asset_ids``) so the Scout can subscribe to
live order books instead of placeholders. The Gamma markets endpoint returns
``clobTokenIds`` and ``outcomes`` as JSON-encoded string arrays, aligned by
index (token i is the order book for outcome i).

Field shapes verified against https://gamma-api.polymarket.com/markets
(``clobTokenIds`` = JSON string array, ``outcomes`` = e.g. ``["Yes","No"]``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import requests

from ..logging_conf import get_logger
from ..matching import canonical_event_id

log = get_logger("polymarket")

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

# Slug-prefix -> canonical sport token. Unknown prefixes pass through unchanged
# (still yields a consistent event_id, just won't align with an Oracle sport).
LEAGUE_SPORT = {"nba": "nba", "nfl": "nfl", "mlb": "mlb", "nhl": "nhl"}
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass
class PolyMarket:
    slug: str
    question: str
    outcomes: list[str]
    token_ids: list[str]


@dataclass
class GameMeta:
    """Canonical identity for a head-to-head market, derived from a PolyMarket."""

    event_id: str
    matchup: str   # "<away> @ <home>"
    away: str
    home: str
    sport: str
    date: str      # YYYY-MM-DD


def _as_list(value) -> list:
    """Gamma returns these as JSON-encoded strings; tolerate real lists too."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return value or []


def parse_markets(raw: list[dict]) -> list[PolyMarket]:
    """Pure parse of Gamma market objects into PolyMarket records (network-free)."""
    markets: list[PolyMarket] = []
    for m in raw:
        token_ids = [str(t) for t in _as_list(m.get("clobTokenIds"))]
        outcomes = [str(o) for o in _as_list(m.get("outcomes"))]
        if not token_ids:
            continue
        markets.append(PolyMarket(
            slug=m.get("slug", ""), question=m.get("question", ""),
            outcomes=outcomes, token_ids=token_ids,
        ))
    return markets


def token_map(markets: list[PolyMarket]) -> dict[str, tuple[str, str]]:
    """{token_id: (slug, outcome_label)} so the Scout can label book updates."""
    mapping: dict[str, tuple[str, str]] = {}
    for m in markets:
        for i, token in enumerate(m.token_ids):
            outcome = m.outcomes[i] if i < len(m.outcomes) else f"outcome{i}"
            mapping[token] = (m.slug, outcome)
    return mapping


def parse_game_market(market: PolyMarket) -> Optional[GameMeta]:
    """Derive canonical identity from a head-to-head market, or None.

    Real Gamma head-to-head markets carry both competitors directly in
    ``outcomes`` (e.g. ``["Royal Challengers Bengaluru", "Gujarat Titans"]``) with
    the date in the slug (``...-2026-05-31``). Futures/props are ``["Yes","No"]``
    and are skipped. Polymarket doesn't expose home/away, so we adopt the
    convention ``away, home = outcomes[0], outcomes[1]`` — good enough for the
    Engine to *price* the market; cross-venue arb alignment stays best-effort.
    """
    outs = [o.strip() for o in market.outcomes if o and o.strip()]
    if len(outs) != 2:
        return None
    if {o.lower() for o in outs} & {"yes", "no"}:
        return None  # futures / binary props, not a game
    date_match = _DATE_RE.search(market.slug or "")
    if not date_match:
        return None  # no date -> can't build a dated, alignable event_id
    date = date_match.group(1)
    league = (market.slug or "").split("-", 1)[0].lower()
    sport = LEAGUE_SPORT.get(league, league)
    away, home = outs[0], outs[1]
    return GameMeta(
        event_id=canonical_event_id(sport, date, away, home),
        matchup=f"{away} @ {home}", away=away, home=home, sport=sport, date=date,
    )


def token_meta(markets: list[PolyMarket]) -> dict[str, tuple[str, Optional[GameMeta]]]:
    """{token_id: (outcome_team, GameMeta|None)} for the Scout.

    ``GameMeta`` is shared by both tokens of a head-to-head market (so either
    side resolves to the same canonical game); it's ``None`` for markets we can't
    identify (futures, undated), in which case the Scout falls back to a minimal,
    unpriced signal.
    """
    mapping: dict[str, tuple[str, Optional[GameMeta]]] = {}
    for m in markets:
        meta = parse_game_market(m)
        for i, token in enumerate(m.token_ids):
            outcome = m.outcomes[i] if i < len(m.outcomes) else f"outcome{i}"
            mapping[token] = (outcome, meta)
    return mapping


def fetch_markets(limit: int = 100, tag: str | None = None) -> list[PolyMarket]:
    """Fetch active, open markets from Gamma. Returns [] on any network error."""
    params = {"active": "true", "closed": "false", "limit": limit, "order": "volume24hr", "ascending": "false"}
    if tag:
        params["tag_slug"] = tag
    try:
        resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
        resp.raise_for_status()
        markets = parse_markets(resp.json())
        log.info("Discovered %d Polymarket markets (%d tokens)",
                 len(markets), sum(len(m.token_ids) for m in markets))
        return markets
    except Exception as exc:  # noqa: BLE001
        log.error("Polymarket discovery failed: %s", exc)
        return []
