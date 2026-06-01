"""Repository layer — the only place that knows SQL.

Centralizes every query against the normalized ``events`` / ``signals`` /
``trades`` schema and replaces the original fragile
``market_id LIKE '%' || event_id || '%'`` joins with real foreign-key joins on
``event_id``. Agents and tools call these typed methods rather than embedding
SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .db import Database

HOME, AWAY = "HOME", "AWAY"


def parse_market_id(market_id: str) -> tuple[str, str, str]:
    """Split ``SOURCE-EVENTID-PARTICIPANT`` -> (source, event_id, participant).

    The participant may itself contain hyphens; only the first two ``-`` are
    structural, so we split at most twice.
    """
    parts = market_id.split("-", 2)
    if len(parts) < 3:
        raise ValueError(f"malformed market_id: {market_id!r}")
    return parts[0], parts[1], parts[2]


def side_for(participant: str, home_team: str) -> str:
    return HOME if participant == home_team else AWAY


@dataclass
class PendingTrade:
    trade_id: int
    side: str
    executed_odds: float
    stake_frac: float
    market_id: Optional[str]
    home_score: int
    away_score: int


class Store:
    def __init__(self, db: Database):
        self.db = db

    @property
    def available(self) -> bool:
        return self.db.available

    # -- events ---------------------------------------------------------------
    def upsert_event_stub(self, event_id: str, home_team: str, away_team: str,
                          sport_id: Optional[int] = None, event_date=None) -> None:
        """Ensure a SCHEDULED event row exists so signals/trades can reference it."""
        self.db.execute(
            """
            INSERT INTO events (event_id, sport_id, event_date, home_team, away_team, status)
            VALUES (%s, %s, %s, %s, %s, 'SCHEDULED')
            ON CONFLICT (event_id) DO UPDATE SET
                home_team = EXCLUDED.home_team,
                away_team = EXCLUDED.away_team,
                updated_at = now()
            """,
            (event_id, sport_id, event_date, home_team, away_team),
        )

    def upsert_event_result(self, event_id, sport_id, event_date, home_team, away_team,
                            home_score, away_score, home_close, away_close) -> None:
        """Record (or complete) a finished game with scores and closing lines."""
        self.db.execute(
            """
            INSERT INTO events (event_id, sport_id, event_date, home_team, away_team,
                                status, home_score, away_score, home_close, away_close)
            VALUES (%s, %s, %s, %s, %s, 'FINAL', %s, %s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET
                status = 'FINAL', home_score = EXCLUDED.home_score,
                away_score = EXCLUDED.away_score, home_close = EXCLUDED.home_close,
                away_close = EXCLUDED.away_close, updated_at = now()
            """,
            (event_id, sport_id, event_date, home_team, away_team,
             home_score, away_score, home_close, away_close),
        )

    def update_closing_odds(self, event_id: str, home_close: float, away_close: float) -> None:
        """Attach real closing decimal odds to an existing game (for CLV / ROI).

        Only touches a known event row — closing lines decorate games we already
        ingested rather than create stubs. The columns already exist in the
        schema; populating them is what unblocks ``make clv`` and a real (vs.
        synthetic-bracket) odds backtest.
        """
        self.db.execute(
            "UPDATE events SET home_close = %s, away_close = %s, updated_at = now() "
            "WHERE event_id = %s",
            (float(home_close), float(away_close), event_id),
        )

    def events_with_closing_odds(self) -> list[tuple]:
        """(event_id, home_team, away_team, event_date, home_close, away_close,
        home_score, away_score) for FINAL games that have real closing lines."""
        return self.db.query(
            "SELECT event_id, home_team, away_team, event_date, home_close, away_close, "
            "home_score, away_score FROM events "
            "WHERE status = 'FINAL' AND home_close > 0 AND away_close > 0"
        )

    def final_events(self, since=None) -> list[tuple]:
        """(event_id, home_team, away_team, event_date) for graded games.

        ``since`` (a timestamp) restricts to recent games — the regime where the
        model's current team_state snapshot is actually valid.
        """
        if since is not None:
            return self.db.query(
                "SELECT event_id, home_team, away_team, event_date FROM events "
                "WHERE status = 'FINAL' AND home_score IS NOT NULL AND event_date >= %s",
                (since,),
            )
        return self.db.query(
            "SELECT event_id, home_team, away_team, event_date FROM events "
            "WHERE status = 'FINAL' AND home_score IS NOT NULL"
        )

    def max_event_date(self):
        return self.db.query_one(
            "SELECT max(event_date) FROM events WHERE status = 'FINAL' AND home_score IS NOT NULL"
        )

    # -- signals --------------------------------------------------------------
    def clear_signals(self, source: str) -> None:
        """Delete signals from one source (so a backfill is idempotent)."""
        self.db.execute("DELETE FROM signals WHERE source = %s", (source,))

    def record_signal(self, event_id, side, source, odds, true_prob, ev) -> None:
        self.db.execute(
            "INSERT INTO signals (event_id, side, source, odds, true_prob, ev) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (event_id, side, source, float(odds), float(true_prob), float(ev)),
        )

    # -- trades ---------------------------------------------------------------
    def record_trade(self, event_id, side, source, executed_odds, stake_frac, status,
                     market_id=None, is_arb=False) -> None:
        self.db.execute(
            "INSERT INTO trades (event_id, side, market_id, source, executed_odds, stake_frac, status, is_arb) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (event_id, side, market_id, source, executed_odds, stake_frac, status, is_arb),
        )

    def pending_settlements(self) -> list[PendingTrade]:
        """Open trades whose event is FINAL — a real FK join, no LIKE matching."""
        rows = self.db.query(
            """
            SELECT t.id, t.side, t.executed_odds, t.stake_frac, t.market_id, e.home_score, e.away_score
            FROM trades t
            JOIN events e ON e.event_id = t.event_id
            WHERE t.status IN ('OPEN', 'ARB_LEG') AND e.status = 'FINAL'
            """
        )
        return [PendingTrade(*r) for r in rows]

    def settle_trade(self, trade_id: int, status: str, pnl: float) -> None:
        self.db.execute(
            "UPDATE trades SET status = %s, pnl = %s, settled_ts = now() WHERE id = %s",
            (status, pnl, trade_id),
        )

    # -- analytics reads ------------------------------------------------------
    def clv_rows(self) -> list[tuple]:
        """(executed_odds, side, home_close, away_close) for settled/open trades."""
        return self.db.query(
            """
            SELECT t.executed_odds, t.side, e.home_close, e.away_close
            FROM trades t JOIN events e ON e.event_id = t.event_id
            WHERE e.status = 'FINAL' AND e.home_close > 0 AND e.away_close > 0
              AND t.status IN ('WIN', 'LOSS', 'OPEN') AND t.executed_odds > 0
            """
        )

    def signal_outcome_rows(self) -> list[tuple]:
        """(true_prob, side, home_score, away_score) for signals on FINAL events."""
        return self.db.query(
            """
            SELECT s.true_prob, s.side, e.home_score, e.away_score
            FROM signals s JOIN events e ON e.event_id = s.event_id
            WHERE e.status = 'FINAL' AND e.home_score IS NOT NULL
            """
        )

    def digest_counts(self, window_hours: int = 24) -> dict:
        """Activity over the trailing window for the daily digest.

        Realized PnL and settled count use ``settled_ts``; trade/signal counts
        use their creation timestamps. All in one round trip.
        """
        row = self.db.query_one(
            """
            SELECT
              COALESCE((SELECT SUM(pnl) FROM trades
                        WHERE settled_ts >= now() - make_interval(hours => %s)), 0),
              (SELECT COUNT(*) FROM trades
                        WHERE settled_ts >= now() - make_interval(hours => %s)),
              (SELECT COUNT(*) FROM trades
                        WHERE executed_ts >= now() - make_interval(hours => %s)),
              (SELECT COUNT(*) FROM signals
                        WHERE ts >= now() - make_interval(hours => %s))
            """,
            (window_hours, window_hours, window_hours, window_hours),
        )
        pnl, settled, trades, signals = row if row else (0, 0, 0, 0)
        return {"realized_pnl": float(pnl or 0), "settled": int(settled or 0),
                "trades": int(trades or 0), "signals": int(signals or 0)}

    # -- web dashboard --------------------------------------------------------
    def dashboard_data(self) -> tuple[list[dict], list[dict], list[dict]]:
        """(signals, events, trades) as JSON-native dicts for the web dashboard.

        One place, real FK joins, timestamps stringified so the payload is
        directly serializable. Bounded by LIMITs — this feeds a live view, not a
        full export.
        """
        def _iso(ts):
            return ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts is not None else None)

        sig_rows = self.db.query(
            """
            SELECT s.ts, e.away_team, e.home_team, s.side, s.source, s.odds, s.true_prob, s.ev,
                   EXISTS(SELECT 1 FROM trades t WHERE t.event_id = s.event_id AND t.side = s.side)
            FROM signals s JOIN events e ON e.event_id = s.event_id
            ORDER BY s.ts DESC LIMIT 500
            """
        )
        signals = [{"ts": _iso(r[0]), "event": f"{r[1]} @ {r[2]}", "side": r[3],
                    "source": r[4], "odds": float(r[5]) if r[5] is not None else None,
                    "true_prob": float(r[6]) if r[6] is not None else None,
                    "ev": float(r[7]) if r[7] is not None else None, "bet": bool(r[8])}
                   for r in sig_rows]

        evt_rows = self.db.query(
            """
            SELECT event_id, home_team, away_team, home_score, away_score, home_close, away_close
            FROM events WHERE status = 'FINAL' AND home_score IS NOT NULL LIMIT 5000
            """
        )
        events = [{"event_id": r[0], "home": r[1], "away": r[2],
                   "home_score": r[3], "away_score": r[4],
                   "home_close": float(r[5]) if r[5] is not None else None,
                   "away_close": float(r[6]) if r[6] is not None else None}
                  for r in evt_rows]

        trade_rows = self.db.query(
            """
            SELECT t.executed_ts, t.market_id, e.away_team, e.home_team, t.side,
                   t.executed_odds, t.stake_frac, t.status, t.pnl, t.is_arb,
                   CASE WHEN t.side = 'HOME' THEN e.home_close ELSE e.away_close END
            FROM trades t JOIN events e ON e.event_id = t.event_id
            ORDER BY t.executed_ts DESC LIMIT 2000
            """
        )
        trades = []
        for r in trade_rows:
            odds = float(r[5]) if r[5] is not None else 0.0
            side_close = float(r[10]) if r[10] is not None else 0.0
            trades.append({"ts": _iso(r[0]), "market_id": r[1], "event": f"{r[2]} @ {r[3]}",
                           "side": r[4], "odds": odds, "stake": float(r[6]) if r[6] is not None else 0.0,
                           "status": r[7], "pnl": float(r[8]) if r[8] is not None else 0.0,
                           "clv": (odds / side_close - 1) if side_close > 0 and odds > 0 else None,
                           "is_arb": bool(r[9])})
        return signals, events, trades

    # -- team stats -----------------------------------------------------------
    def team_stat(self, team_name: str):
        """(net_rating, pace, player_strength) for a team, or None."""
        return self.db.query_one(
            "SELECT net_rating, pace, player_strength FROM team_advanced_stats "
            "WHERE team_name ILIKE %s LIMIT 1",
            (f"%{team_name}%",),
        )

    def team_stats_all(self) -> list[tuple]:
        """(team_name, net_rating, pace, player_strength) for every team."""
        return self.db.query(
            "SELECT team_name, net_rating, pace, player_strength FROM team_advanced_stats"
        )

    def roster_pit_all(self) -> list[tuple]:
        """(team_name, game_date, roster_strength) point-in-time, every team-game."""
        return self.db.query(
            "SELECT team_name, game_date, roster_strength FROM team_strength_pit"
        )

    # -- availability ---------------------------------------------------------
    def availability_pit_all(self) -> list[tuple]:
        """(team_name, game_date, availability) point-in-time, every team-game."""
        return self.db.query(
            "SELECT team_name, game_date, availability FROM team_availability_pit"
        )

    def upsert_availability(self, team_name: str, game_date, season, availability: float) -> None:
        """Record one team's point-in-time roster availability for a date."""
        self.db.execute(
            """
            INSERT INTO team_availability_pit (team_name, game_date, season, availability)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (team_name, game_date) DO UPDATE SET
                season = EXCLUDED.season, availability = EXCLUDED.availability
            """,
            (team_name, game_date, season, float(availability)),
        )

    def team_availability(self, team_name: str):
        """Most recent availability for a team (tonight's, for serving), or None."""
        return self.db.query_one(
            "SELECT availability FROM team_availability_pit "
            "WHERE team_name ILIKE %s ORDER BY game_date DESC LIMIT 1",
            (f"%{team_name}%",),
        )
