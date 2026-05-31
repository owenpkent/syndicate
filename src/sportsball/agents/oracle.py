"""Oracle Agent — ingests sharp-book market lines.

Pulls moneyline odds from The Rundown (NBA by default) and publishes normalized
``market_signal`` messages. Unlike the original, the Oracle does **not** invent a
``true_prob`` — it carries only the market price and matchup metadata. Modeling
the probability is the Analytics Engine's job, so a missing model means "no
edge / no bet" rather than "trade on a random number".
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from ..broker import Broker, MARKET_SIGNALS
from ..config import Settings, load_settings
from ..logging_conf import get_logger
from ..quant.odds import american_to_decimal

log = get_logger("oracle")

RUNDOWN_NBA_SPORT_ID = 4
PREFERRED_AFFILIATE = "19"  # Pinnacle, as an example sharp book


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_signal(source: str, event_id: str, away: str, home: str,
                 participant: str, decimal_odds: float, affiliate_id: str) -> dict:
    """Construct a schema-compliant market signal (see docs/ARCHITECTURE.md §4.2)."""
    return {
        "market_id": f"{source}-{event_id}-{participant}",
        "odds": decimal_odds,
        "metadata": {
            "source": source,
            "matchup": f"{away} @ {home}",
            "participant": participant,
            "affiliate_id": affiliate_id,
        },
    }


def fetch_rundown_markets(api_key: str) -> list[dict] | None:
    url = f"https://therundown.io/api/v2/sports/{RUNDOWN_NBA_SPORT_ID}/events/{_today()}"
    try:
        resp = requests.get(url, headers={"X-TheRundown-Key": api_key}, timeout=10)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to fetch from The Rundown: %s", exc)
        return None

    signals: list[dict] = []
    for event in events:
        event_id = event.get("event_id")
        teams = event.get("teams", [])
        home = next((t["name"] for t in teams if not t.get("is_away")), "Home")
        away = next((t["name"] for t in teams if t.get("is_away")), "Away")
        moneyline = next((m for m in event.get("markets", []) if m.get("market_id") == 1), None)
        if not moneyline:
            continue
        for participant in moneyline.get("participants", []):
            lines = participant.get("lines", [])
            if not lines:
                continue
            prices = lines[0].get("prices", {})
            aff = PREFERRED_AFFILIATE if PREFERRED_AFFILIATE in prices else next(iter(prices), None)
            if not aff:
                continue
            american = prices[aff].get("price")
            if not american or american == 0.0001:  # off-board sentinel
                continue
            signals.append(build_signal(
                "RUNDOWN", event_id, away, home, participant["name"],
                american_to_decimal(american), aff,
            ))
    return signals


def fetch_mock_lines() -> list[dict]:
    """Deterministic mock slate (no randomness) for offline runs.

    Each game emits both sides so the arbitrage book has two participants to
    compare. ``games`` is (event_id, away, home, away_odds, home_odds).
    """
    log.info("Oracle: using mock mode (no live key).")
    games = [
        ("MOCK-001", "Lakers", "Celtics", 2.10, 1.80),
        ("MOCK-002", "Warriors", "Nets", 1.95, 1.95),
    ]
    signals: list[dict] = []
    for event_id, away, home, away_odds, home_odds in games:
        signals.append(build_signal("MOCK", event_id, away, home, away, away_odds, "mock"))
        signals.append(build_signal("MOCK", event_id, away, home, home, home_odds, "mock"))
    return signals


def run(settings: Settings) -> None:
    broker = Broker(settings.redis)
    while True:
        signals = None
        if settings.has_live_rundown_key():
            log.info("Oracle: fetching live lines from The Rundown...")
            signals = fetch_rundown_markets(settings.rundown_api_key)
        if signals is None:
            signals = fetch_mock_lines()
        for sig in signals:
            broker.push(MARKET_SIGNALS, sig)
            log.info("Pushed %s @ %s", sig["market_id"], sig["odds"])
        time.sleep(settings.polling_interval)


def main() -> None:
    log.info("Oracle Agent starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
