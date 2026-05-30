import asyncio
import json
import os
import redis.asyncio as redis
import websockets
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ScoutAgent")

POLYMARKET_WS_URL = "wss://clob.polymarket.com/ws"

async def handle_market_update(data, r):
    """
    Processes a raw WebSocket message and pushes liquidity signals to Redis.
    """
    try:
        if data.get("type") == "book":
            market_id = data.get("asset_id")
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                mid_price = (best_bid + best_ask) / 2
                
                # In Polymarket, prices are between 0 and 1 (implied probability)
                # We can convert this to decimal odds: Odds = 1 / Price
                implied_odds = round(1 / mid_price, 4) if mid_price > 0 else 0
                
                signal = {
                    "market_id": f"POLY-{market_id}",
                    "true_prob": mid_price, # Using mid-price as a proxy for market consensus
                    "odds": implied_odds,
                    "metadata": {
                        "source": "Polymarket",
                        "best_bid": best_bid,
                        "best_ask": best_ask
                    }
                }
                
                await r.rpush("market_signals", json.dumps(signal))
                logger.info(f"Pushed Polymarket signal for {market_id} | Price: {mid_price:.4f}")
                
    except Exception as e:
        logger.error(f"Error handling market update: {e}")

async def monitor_polymarket():
    logger.info("Scout Agent starting (Polymarket WebSocket)...")
    
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    
    while True:
        try:
            async with websockets.connect(POLYMARKET_WS_URL) as ws:
                logger.info("Connected to Polymarket CLOB WebSocket")
                
                # Subscription logic (Example IDs, in production these would be dynamic)
                # Subscribing to a few major markets
                subscribe_msg = {
                    "type": "subscribe",
                    "channels": ["book"],
                    "assets_ids": ["123456", "789012"] # Placeholder IDs
                }
                await ws.send(json.dumps(subscribe_msg))
                
                async for message in ws:
                    data = json.loads(message)
                    await handle_market_update(data, r)
                    
        except websockets.ConnectionClosed:
            logger.warning("WebSocket connection closed. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Scout Agent Connection Error: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(monitor_polymarket())
