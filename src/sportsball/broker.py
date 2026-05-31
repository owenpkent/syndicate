"""Redis broker abstraction.

The old agents busy-polled with ``lpop`` + ``time.sleep`` and lost any in-flight
message if the consumer crashed between pop and processing. This wraps the same
Redis Lists with:

* **blocking reads** (``BLPOP``) so consumers don't spin the CPU, and
* an optional **reliable** mode (``BRPOPLPUSH`` into a per-consumer in-flight
  list + explicit ack) so a crash mid-processing re-queues the message instead
  of dropping it.

It also owns the ``active_trades`` exposure hash, including the reaper that the
old system was missing (exposure used to accumulate forever).
"""
from __future__ import annotations

import json
from typing import Iterator, Optional

import redis

from .config import RedisConfig
from .logging_conf import get_logger

log = get_logger("broker")

MARKET_SIGNALS = "market_signals"
EXECUTION_SIGNALS = "execution_signals"
ACTIVE_TRADES = "active_trades"


class Broker:
    def __init__(self, config: Optional[RedisConfig] = None):
        cfg = config or RedisConfig()
        self.r = redis.Redis(host=cfg.host, port=cfg.port, db=cfg.db, decode_responses=True)

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except redis.RedisError:
            return False

    # -- queues ---------------------------------------------------------------
    def push(self, queue: str, payload: dict) -> None:
        self.r.rpush(queue, json.dumps(payload))

    def pop(self, queue: str, block: bool = True, timeout: int = 5) -> Optional[dict]:
        """Pop one item. Blocks up to ``timeout`` s; returns None on timeout."""
        if block:
            result = self.r.blpop(queue, timeout=timeout)
            raw = result[1] if result else None
        else:
            raw = self.r.lpop(queue)
        return json.loads(raw) if raw else None

    def reliable_consume(
        self, queue: str, inflight: str, timeout: int = 5
    ) -> Iterator[tuple[str, dict]]:
        """Yield ``(raw, payload)`` using a reliable-queue pattern.

        Each message is atomically moved to ``inflight`` until ``ack``-ed, so a
        crash leaves it recoverable rather than lost. Call :meth:`ack` after a
        message is fully processed.
        """
        # Recover anything left in-flight from a previous crash first.
        while (raw := self.r.lindex(inflight, -1)) is not None:
            yield raw, json.loads(raw)
        while True:
            raw = self.r.brpoplpush(queue, inflight, timeout=timeout)
            if raw is None:
                continue
            yield raw, json.loads(raw)

    def ack(self, inflight: str, raw: str) -> None:
        self.r.lrem(inflight, 1, raw)

    def queue_depth(self, queue: str) -> int:
        return int(self.r.llen(queue))

    # -- exposure hash --------------------------------------------------------
    def set_exposure(self, market_id: str, size: float) -> None:
        self.r.hset(ACTIVE_TRADES, market_id, size)

    def clear_exposure(self, market_id: str) -> None:
        self.r.hdel(ACTIVE_TRADES, market_id)

    def active_trades(self) -> list[dict]:
        raw = self.r.hgetall(ACTIVE_TRADES)
        return [{"market_id": k, "size": float(v)} for k, v in raw.items()]

    def total_exposure(self) -> float:
        return sum(t["size"] for t in self.active_trades())
