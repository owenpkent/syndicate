"""Shared plumbing for the DeFi time-series collectors.

This is the DeFi pivot of the project: the real goal is honing **time-series
prediction**, and the target domain is decentralized finance (on-chain perps,
prediction markets, DEX/CEX microstructure). Sports odds were the scaffolding.

These collectors all follow the same idiom as the sports odds crons
(scripts/capture_snapshot.py): pull a live snapshot, append append-only rows
keyed by ``captured_at`` into DuckDB, log one line. They write to a SEPARATE
store (``data/defi.duckdb``) so their dense intraday cadence never contends with
the sports odds writers.

Lead-lag (which venue moves first) is not a separate collector — it falls out of
capturing Hyperliquid mark price (capture_hyperliquid) and CEX spot (capture_cex)
densely with timestamps, then joining on time.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

from sportsball.logging_conf import get_logger  # noqa: E402
from capture_odds_quotes import connect_duckdb  # noqa: E402  (retry-on-lock connect)

DEFI_DB = str(_ROOT / "data" / "defi.duckdb")
UA = {"User-Agent": "sportsball-defi/0.1"}


def now_utc() -> datetime:
    """Naive UTC timestamp (matches how DuckDB stores the sports snapshots)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def http(method: str, url: str, *, json=None, params=None, timeout: int = 15,
         retries: int = 3, log=None):
    """GET/POST with small backoff on transient network/5xx errors.

    Collectors are best-effort cron jobs: a single failed snapshot is acceptable
    (the series is dense), so on exhaustion we raise and let the caller log+skip.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = (requests.post(url, json=json, headers=UA, timeout=timeout)
                 if method == "POST" else
                 requests.get(url, params=params, headers=UA, timeout=timeout))
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]
