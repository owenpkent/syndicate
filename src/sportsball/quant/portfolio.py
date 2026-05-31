"""Portfolio-level risk coordination.

Clamps a proposed Kelly stake against a global exposure ceiling and applies a
correlation penalty when a new bet shares an event with an open position.
"""
from __future__ import annotations

from ..config import StrategyConfig
from ..logging_conf import get_logger

log = get_logger("portfolio")


def _event_of(market_id: str) -> str:
    parts = market_id.split("-")
    return parts[1] if len(parts) > 1 else market_id


class PortfolioRiskManager:
    def __init__(self, strategy: StrategyConfig):
        self.max_exposure = strategy.max_global_exposure_pct
        self.correlation_penalty = strategy.correlation_penalty_multiplier

    def evaluate_risk(self, market_id: str, proposed_size: float, active_trades: list[dict]) -> float:
        """Return the size to actually stake (0 if rejected)."""
        current = sum(t.get("size", 0) for t in active_trades)
        log.info("Current portfolio exposure: %.4f", current)

        # Global exposure ceiling.
        if current + proposed_size > self.max_exposure:
            available = max(0.0, self.max_exposure - current)
            if available <= 0:
                log.warning("[RISK] Rejecting %s: global exposure limit reached.", market_id)
                return 0.0
            log.warning(
                "[RISK] Downsizing %s: %.4f -> %.4f (global limit)",
                market_id, proposed_size, available,
            )
            proposed_size = available

        # Correlation guard: already exposed to this event?
        event_id = _event_of(market_id)
        if any(event_id in t.get("market_id", "") for t in active_trades):
            log.info("[CORRELATION] Penalizing %s: existing exposure for event.", market_id)
            proposed_size *= self.correlation_penalty

        return proposed_size
