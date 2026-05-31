"""Cross-venue arbitrage detection.

Maintains a best-price book per event and flags risk-free opportunities where
the implied probabilities of the mutually exclusive outcomes sum to < 1.
"""
from __future__ import annotations

from typing import Optional

from ..logging_conf import get_logger

log = get_logger("arbitrage")


class ArbitrageEngine:
    def __init__(self):
        # {event_id: {"Home": {odds, source, market_id}, "Away": {...}}}
        self.order_book: dict[str, dict] = {}

    def update_odds(
        self, market_id: str, odds: float, source: str, participant_type: str
    ) -> Optional[str]:
        """Record a price, keeping the best (highest) per side. Returns event id.

        ``market_id`` must follow ``SOURCE-EVENTID-TEAM``; anything with fewer
        than three ``-`` segments is ignored (returns None).
        """
        parts = market_id.split("-")
        if len(parts) < 3:
            return None
        event_id = parts[1]

        book = self.order_book.setdefault(event_id, {"Home": None, "Away": None})
        current = book.get(participant_type)
        if current is None or odds > current["odds"]:
            book[participant_type] = {"odds": odds, "source": source, "market_id": market_id}
            log.info("Best %s odds for %s: %s @ %s", participant_type, event_id, odds, source)
        return event_id

    def check_arbitrage(self, event_id: str) -> Optional[dict]:
        """Return an opportunity dict if ``Σ(1/odds) < 1``, else None."""
        book = self.order_book.get(event_id)
        if not book or not book.get("Home") or not book.get("Away"):
            return None

        o_home = book["Home"]["odds"]
        o_away = book["Away"]["odds"]
        s = (1 / o_home) + (1 / o_away)
        if s >= 1.0:
            return None

        return {
            "event_id": event_id,
            "profit_margin": 1.0 - s,
            "arb_percent": s,
            "legs": [
                {**book["Home"], "side": "HOME", "allocation": (1 / o_home) / s},
                {**book["Away"], "side": "AWAY", "allocation": (1 / o_away) / s},
            ],
        }
