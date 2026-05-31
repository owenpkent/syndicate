"""Scout Agent — the watcher.

Maintains a WebSocket connection to Polymarket's CLOB, translating order-book
mid-prices into ``market_signal`` messages.

NOTE: live market discovery (resolving real ``asset_ids`` via the Gamma API) is
Phase 3 work. Today this connects and parses ``book`` messages correctly, but
the subscription uses placeholder asset ids and will not produce signals until
real ids are supplied via ``SCOUT_ASSET_IDS`` (comma-separated). This is called
out in docs/ARCHITECTURE.md §5.
"""
from __future__ import annotations

import asyncio
import json
import os

import redis.asyncio as redis
import websockets

from ..config import RedisConfig
from ..broker import MARKET_SIGNALS
from ..logging_conf import get_logger
from ..quant.odds import implied_prob

log = get_logger("scout")

POLYMARKET_WS_URL = os.getenv("POLYMARKET_WS_URL", "wss://clob.polymarket.com/ws")


def parse_book(data: dict) -> dict | None:
    """Translate a Polymarket ``book`` message into a market signal, or None."""
    if data.get("type") != "book":
        return None
    bids, asks = data.get("bids", []), data.get("asks", [])
    if not bids or not asks:
        return None
    best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
    mid = (best_bid + best_ask) / 2
    if mid <= 0:
        return None
    return {
        "market_id": f"POLY-{data.get('asset_id')}",
        "odds": round(1 / mid, 4),
        "metadata": {"source": "Polymarket", "best_bid": best_bid,
                     "best_ask": best_ask, "mid_implied_prob": round(mid, 4)},
    }


async def monitor(redis_cfg: RedisConfig) -> None:
    r = redis.Redis(host=redis_cfg.host, port=redis_cfg.port, db=redis_cfg.db, decode_responses=True)
    asset_ids = [a for a in os.getenv("SCOUT_ASSET_IDS", "123456,789012").split(",") if a]
    while True:
        try:
            async with websockets.connect(POLYMARKET_WS_URL) as ws:
                log.info("Connected to Polymarket CLOB (%d assets)", len(asset_ids))
                await ws.send(json.dumps({"type": "subscribe", "channels": ["book"],
                                          "assets_ids": asset_ids}))
                async for message in ws:
                    sig = parse_book(json.loads(message))
                    if sig:
                        await r.rpush(MARKET_SIGNALS, json.dumps(sig))
                        log.info("Pushed %s", sig["market_id"])
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
