"""Scout Agent — the watcher (live Polymarket order books).

Discovers real markets via the Gamma API, subscribes to the CLOB market channel,
and translates order-book mid-prices into ``market_signal`` messages.

Verified against Polymarket's API:
* WS URL ``wss://ws-subscriptions-clob.polymarket.com/ws/market``
* subscribe with ``{"assets_ids": [...], "type": "market"}``
* ``book`` messages carry ``event_type``, ``asset_id``, and ``bids``/``asks`` as
  ``[{"price": "0.48", "size": "30"}, ...]``.

Set ``SCOUT_ASSET_IDS`` (comma-separated) to override discovery with specific
token ids. Cross-venue arbitrage against the Oracle requires the same canonical
``event_id`` on both sides — for Polymarket that needs a sports market whose
matchup is parseable, which is best-effort (see docs/ARCHITECTURE.md §5).
"""
from __future__ import annotations

import asyncio
import json
import os

import redis.asyncio as redis
import websockets

from ..broker import MARKET_SIGNALS
from ..config import RedisConfig
from ..logging_conf import get_logger
from ..markets.polymarket import GameMeta, fetch_markets, token_meta

log = get_logger("scout")

CLOB_WS_URL = os.getenv("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
DISCOVERY_LIMIT = int(os.getenv("SCOUT_DISCOVERY_LIMIT", "50"))


def _best(levels: list[dict], side: str) -> float | None:
    """Best price on a side: highest bid, lowest ask. Levels are {price,size} dicts."""
    prices = [float(lvl["price"]) for lvl in levels if float(lvl.get("size", 0)) > 0]
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


def parse_book(data: dict, labels: dict[str, tuple[str, GameMeta | None]] | None = None) -> dict | None:
    """Translate a CLOB ``book`` message into a market signal, or None.

    When the token resolves to a :class:`GameMeta` (an identified head-to-head
    game) the signal carries the canonical ``event_id``, ``matchup`` and
    ``participant`` so the Engine can *price* it via the model and it aligns with
    the Oracle/Settlement contract. Otherwise it falls back to a minimal,
    unpriced signal (the Engine abstains), preserving today's behavior.
    """
    if data.get("event_type") != "book":
        return None
    asset_id = data.get("asset_id")
    best_bid = _best(data.get("bids", []), "bid")
    best_ask = _best(data.get("asks", []), "ask")
    if best_bid is None or best_ask is None:
        return None
    mid = (best_bid + best_ask) / 2
    if mid <= 0:
        return None
    outcome, meta = (labels or {}).get(asset_id, ("", None))
    odds = round(1 / mid, 4)
    prices = {"best_bid": best_bid, "best_ask": best_ask, "mid_implied_prob": round(mid, 4)}

    if meta is not None:
        # Identified game: priceable + canonically keyed.
        return {
            "market_id": f"POLY-{meta.event_id}-{outcome}",
            "odds": odds,
            "metadata": {"source": "Polymarket", "event_id": meta.event_id,
                         "sport": meta.sport, "matchup": meta.matchup,
                         "participant": outcome, "outcome": outcome, **prices},
        }
    return {
        "market_id": f"POLY-{asset_id}-{outcome or 'NA'}",
        "odds": odds,
        "metadata": {"source": "Polymarket", "outcome": outcome, **prices},
    }


def resolve_asset_ids() -> tuple[list[str], dict[str, tuple[str, GameMeta | None]]]:
    """Asset ids to watch + their {token: (outcome_team, GameMeta|None)} labels."""
    override = os.getenv("SCOUT_ASSET_IDS")
    if override:
        ids = [a.strip() for a in override.split(",") if a.strip()]
        return ids, {}
    markets = fetch_markets(limit=DISCOVERY_LIMIT)
    labels = token_meta(markets)
    return list(labels.keys()), labels


async def monitor(redis_cfg: RedisConfig) -> None:
    r = redis.Redis(host=redis_cfg.host, port=redis_cfg.port, db=redis_cfg.db, decode_responses=True)
    asset_ids, labels = resolve_asset_ids()
    if not asset_ids:
        log.warning("No asset ids to subscribe to (discovery empty, no SCOUT_ASSET_IDS).")
    while True:
        try:
            async with websockets.connect(CLOB_WS_URL) as ws:
                log.info("Connected to Polymarket CLOB (%d assets)", len(asset_ids))
                await ws.send(json.dumps({"type": "market", "assets_ids": asset_ids}))
                async for message in ws:
                    sig = parse_book(json.loads(message), labels)
                    if sig:
                        await r.rpush(MARKET_SIGNALS, json.dumps(sig))
                        log.info("Pushed %s @ %s", sig["market_id"], sig["odds"])
        except websockets.ConnectionClosed:
            log.warning("WS closed; reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as exc:  # noqa: BLE001
            log.error("Scout connection error: %s", exc)
            await asyncio.sleep(10)


def main() -> None:
    log.info("Scout Agent starting (Polymarket WebSocket)...")
    asyncio.run(monitor(RedisConfig()))


if __name__ == "__main__":
    main()
