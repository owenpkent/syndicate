"""Daily digest — a scheduled performance summary posted to Slack.

Aggregates the trailing 24h (realized PnL, trade/signal/settled counts), open
exposure, and model freshness, prints it, and posts a Slack card. Designed to be
run on a cron / ``docker compose run --rm digest``; it is a one-shot, not a loop.

``build_summary`` is pure (takes injected store/broker, returns a dataclass) so
the aggregation is unit-testable without infrastructure.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass

from ..broker import Broker
from ..config import Settings, load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..notify import build_notifier
from ..store import Store

log = get_logger("digest")

MODEL_FILE = os.path.join("models", "win_prob_model.pkl")


@dataclass
class DigestSummary:
    realized_pnl: float
    trades: int
    settled: int
    signals: int
    open_exposure: float
    model_age: str

    def as_dict(self) -> dict:
        return asdict(self)


def _model_age(model_file: str, now: float) -> str:
    try:
        age_h = (now - os.path.getmtime(model_file)) / 3600.0
    except OSError:
        return "no model"
    if age_h < 1:
        return "fresh (<1h)"
    if age_h < 48:
        return f"{age_h:.0f}h old"
    return f"{age_h / 24:.0f}d old"


def build_summary(store: Store, broker: Broker, now: float,
                  model_file: str = MODEL_FILE) -> DigestSummary:
    counts = store.digest_counts(24) if store.available else {
        "realized_pnl": 0.0, "trades": 0, "settled": 0, "signals": 0}
    return DigestSummary(
        realized_pnl=counts["realized_pnl"],
        trades=counts["trades"],
        settled=counts["settled"],
        signals=counts["signals"],
        open_exposure=broker.total_exposure(),
        model_age=_model_age(model_file, now),
    )


def run(settings: Settings) -> None:
    store = Store(Database(settings.db))
    store.db.connect()
    broker = Broker(settings.redis)
    summary = build_summary(store, broker, time.time())
    log.info("Digest: pnl=%.4f trades=%d settled=%d signals=%d exposure=%.4f model=%s",
             summary.realized_pnl, summary.trades, summary.settled, summary.signals,
             summary.open_exposure, summary.model_age)
    build_notifier(settings).notify_digest(summary.as_dict())


def main() -> None:
    log.info("Daily digest starting...")
    run(load_settings())


if __name__ == "__main__":
    main()
