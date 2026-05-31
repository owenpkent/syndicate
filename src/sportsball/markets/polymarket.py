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
from dataclasses import dataclass

import requests

from ..logging_conf import get_logger

log = get_logger("polymarket")

GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"


@dataclass
class PolyMarket:
    slug: str
    question: str
    outcomes: list[str]
    token_ids: list[str]


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
