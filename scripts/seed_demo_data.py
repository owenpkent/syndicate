"""Seed the normalized schema with demo data for visualization/testing.

Generates FINAL events (scores + closing lines), the signals the Engine would
have logged, and settled trades with realized PnL — enough to exercise the
dashboard, CLV, evaluation, and equity-curve tools end to end.
"""
import random
from datetime import datetime, timedelta, timezone

from sportsball.config import load_settings
from sportsball.db import Database
from sportsball.store import HOME, Store

N = 500
RNG = random.Random(42)  # deterministic demo


def seed():
    store = Store(Database(load_settings().db))
    if not store.available:
        print("Database unavailable.")
        return
    print(f"Seeding {N} events + signals + trades...")

    trades = 0
    for i in range(N):
        event_id = f"SEED-{i:04d}"
        home, away = "TeamA", "TeamB"
        p_home = RNG.uniform(0.3, 0.7)               # "true" home win prob
        home_won = RNG.random() < p_home             # outcome drawn from it
        h_score, a_score = (110, 100) if home_won else (100, 110)
        home_close = round(1 / max(0.05, p_home + RNG.uniform(-0.08, 0.08)), 2)
        away_close = round(1 / max(0.05, (1 - p_home) + RNG.uniform(-0.08, 0.08)), 2)
        event_date = datetime.now(timezone.utc) - timedelta(days=i)

        store.upsert_event_result(event_id, 4, event_date, home, away,
                                  h_score, a_score, home_close, away_close)

        ev = p_home * home_close - 1
        store.record_signal(event_id, HOME, "SEED", home_close, p_home, ev)

        if ev > 0.03:  # the Engine would have traded this
            stake = 0.02
            pnl = stake * (home_close - 1) if home_won else -stake
            status = "WIN" if home_won else "LOSS"
            store.record_trade(event_id, HOME, "SEED", home_close, stake, status,
                               market_id=f"SEED-{event_id}-TeamA")
            store.db.execute(
                "UPDATE trades SET pnl = %s, settled_ts = now() "
                "WHERE event_id = %s AND pnl IS NULL",
                (pnl, event_id),
            )
            trades += 1

    print(f"Seeded {N} events and {trades} settled trades. "
          "Try 'make dashboard', 'make clv', 'make evaluate', or 'make plot'.")


if __name__ == "__main__":
    seed()
