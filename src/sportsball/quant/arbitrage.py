"""Cross-venue arbitrage detection and best-line book.

Maintains a best-price book per *matchup* and flags risk-free opportunities where
the implied probabilities of the two mutually exclusive outcomes sum to < 1.

Two design points make this work across venues:

* The book is keyed by :func:`matching.matchup_key` (order-independent), not the
  raw oriented ``event_id``. Polymarket doesn't expose home/away, so the same
  game can arrive with the teams in either order; keying on the sorted-token
  matchup key collapses both onto one book so their prices actually meet.
* Within a matchup, outcomes are keyed by **team token** (``normalize_team``),
  not a positional "Home"/"Away" slot — so a venue with the reversed orientation
  still lands its price on the correct team rather than colliding in the wrong
  slot. The producer's HOME/AWAY label rides along on the leg for settlement.
"""
from __future__ import annotations

from typing import Optional

from ..logging_conf import get_logger
from ..matching import matchup_key, normalize_team

log = get_logger("arbitrage")


class ArbitrageEngine:
    def __init__(self):
        # {matchup_key: {team_token: {odds, source, market_id, side, token}}}
        self.order_book: dict[str, dict] = {}

    def update_odds(
        self, market_id: str, odds: float, source: str, side: str
    ) -> Optional[str]:
        """Record a price, keeping the best (highest) per team. Returns book key.

        ``market_id`` must follow ``SOURCE-EVENTID-PARTICIPANT``; anything with
        fewer than three ``-`` segments is ignored (returns None). The book key is
        the order-independent :func:`matchup_key` of EVENTID (falling back to the
        raw id when it isn't canonical), and the outcome is keyed by the
        normalized PARTICIPANT token. ``side`` is the producer's HOME/AWAY label,
        stored verbatim so the winning leg settles against its own event row.
        """
        parts = market_id.split("-", 2)
        if len(parts) < 3:
            return None
        event_id, participant = parts[1], parts[2]
        key = matchup_key(event_id) or event_id
        token = normalize_team(participant) or participant.lower()

        book = self.order_book.setdefault(key, {})
        current = book.get(token)
        if current is None or odds > current["odds"]:
            book[token] = {"odds": odds, "source": source,
                           "market_id": market_id, "side": side, "token": token}
            log.info("Best %s odds for %s: %s @ %s", token, key, odds, source)
        return key

    def best(self, key: str, token: str) -> Optional[dict]:
        """Best stored price for one team token in a matchup, or None.

        Used for cross-book *line shopping*: when the Engine decides to bet a side
        it can fetch the best number any venue is offering on that exact team."""
        return self.order_book.get(key, {}).get(token)

    def check_arbitrage(self, key: str) -> Optional[dict]:
        """Return an opportunity dict if the two outcomes' ``Σ(1/odds) < 1``.

        Requires exactly two distinct team tokens priced (one per outcome). Each
        leg carries the producer's HOME/AWAY ``side`` and its own ``market_id`` so
        the Sniper records and the Settlement grades each leg correctly.
        """
        book = self.order_book.get(key)
        if not book or len(book) != 2:
            return None

        outcomes = list(book.values())
        s = sum(1 / o["odds"] for o in outcomes)
        if s >= 1.0:
            return None

        legs = [{**o, "side": o["side"], "allocation": (1 / o["odds"]) / s}
                for o in outcomes]
        return {
            "event_id": key,
            "profit_margin": 1.0 - s,
            "arb_percent": s,
            "legs": legs,
        }
