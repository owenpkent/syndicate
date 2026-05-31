import logging

logger = logging.getLogger("ArbitrageEngine")

class ArbitrageEngine:
    """
    Detects risk-free arbitrage opportunities across multiple data sources.
    Maintains a cross-venue order book.
    """
    def __init__(self):
        # State: {event_id: {"Home": {"odds": float, "source": str}, "Away": {"odds": float, "source": str}}}
        self.order_book = {}

    def update_odds(self, market_id, odds, source, participant_type):
        """
        Updates the internal state with the latest odds for a participant.
        market_id: Expected format "SOURCE-EVENTID-TEAMNAME"
        participant_type: "Home" or "Away"
        """
        try:
            # Extract common Event ID
            parts = market_id.split("-")
            if len(parts) < 3:
                return
            event_id = parts[1]
            
            if event_id not in self.order_book:
                self.order_book[event_id] = {"Home": None, "Away": None}
            
            # Update only if odds are better than currently known
            current = self.order_book[event_id][participant_type]
            if current is None or odds > current["odds"]:
                self.order_book[event_id][participant_type] = {
                    "odds": odds,
                    "source": source,
                    "market_id": market_id
                }
                logger.info(f"Updated Best {participant_type} Odds for {event_id}: {odds} from {source}")
            
            return event_id
        except Exception as e:
            logger.error(f"Error updating odds in Arb Engine: {e}")
            return None

    def check_arbitrage(self, event_id):
        """
        Checks if an arbitrage exists for the given event.
        S = sum(1/odds). If S < 1, Arb exists.
        """
        book = self.order_book.get(event_id)
        if not book or not book["Home"] or not book["Away"]:
            return None

        o_home = book["Home"]["odds"]
        o_away = book["Away"]["odds"]
        
        arb_percent = (1/o_home) + (1/o_away)
        
        if arb_percent < 1.0:
            profit_margin = (1.0 - arb_percent)
            
            # Calculate optimal allocation percentages
            # allocation_a = (1/O_a) / S
            alloc_home = (1/o_home) / arb_percent
            alloc_away = (1/o_away) / arb_percent
            
            return {
                "event_id": event_id,
                "profit_margin": profit_margin,
                "arb_percent": arb_percent,
                "legs": [
                    {**book["Home"], "allocation": alloc_home},
                    {**book["Away"], "allocation": alloc_away}
                ]
            }
        return None
