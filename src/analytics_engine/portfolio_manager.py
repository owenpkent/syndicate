import logging

logger = logging.getLogger("PortfolioManager")

class PortfolioRiskManager:
    """
    Coordinates risk across the entire trade portfolio.
    Enforces global exposure limits and manages correlated risks.
    """
    def __init__(self, settings):
        self.max_exposure = settings.get("max_global_exposure_pct", 0.15)
        self.correlation_penalty = settings.get("correlation_penalty_multiplier", 0.5)

    def evaluate_risk(self, new_signal, active_trades):
        """
        Adjusts the Kelly fraction based on portfolio-wide state.
        active_trades: List of dicts [{"market_id": str, "size": float, "event_id": str}]
        """
        market_id = new_signal.get("market_id")
        event_id = market_id.split("-")[1] if "-" in market_id else market_id
        proposed_size = new_signal.get("fraction", 0)
        
        # 1. Calculate Current Global Exposure
        current_exposure = sum(t.get("size", 0) for t in active_trades)
        logger.info(f"Current Portfolio Exposure: {current_exposure:.4f}")

        # 2. Check Global Exposure Limit
        if current_exposure + proposed_size > self.max_exposure:
            available_space = max(0, self.max_exposure - current_exposure)
            if available_space <= 0:
                logger.warning(f"[RISK GUARD] Rejecting {market_id}: Global exposure limit reached.")
                return 0
            
            logger.warning(f"[RISK GUARD] Downsizing {market_id}: {proposed_size:.4f} -> {available_space:.4f} (Global Limit)")
            proposed_size = available_space

        # 3. Correlation Guard
        # Check if we already have a trade for this specific event (e.g. ML already placed, now Spread)
        is_correlated = any(event_id in t.get("market_id", "") for t in active_trades)
        
        if is_correlated:
            logger.info(f"[CORRELATION] Applying penalty to {market_id}: Found existing exposure for event.")
            proposed_size = proposed_size * self.correlation_penalty

        return proposed_size
