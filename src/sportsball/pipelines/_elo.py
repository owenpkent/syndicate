"""Shared Elo simulation used by both the optimizer and the trainer.

Both pipelines walk the same historical results forward, updating ratings game
by game. Factoring it here removes the near-duplicate ``simulate_elo`` /
``generate_features`` code the original had in two files.

Beyond plain Elo this now applies a **margin-of-victory** multiplier (so blowouts
move ratings more, with the 538-style correction that damps the favorite's
auto-correlation) and **season carryover** (ratings regress toward 1500 after an
offseason gap). It also emits the full model feature vector per game via the
shared :mod:`sportsball.quant.features` builder, and returns each team's final
:class:`TeamSnapshot` so the trainer can persist it for symmetric serving.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional

import numpy as np

from ..db import Database
from ..quant import features as feat
from ..quant.features import TeamSnapshot

HISTORY_QUERY = """
    SELECT event_date, home_team, away_team, home_score, away_score
    FROM events
    WHERE status = 'FINAL' AND home_score IS NOT NULL
    ORDER BY event_date ASC
"""


class FeatureRow(NamedTuple):
    features: list[float]   # in features.FEATURE_ORDER; features[0] == elo_diff_hfa
    exp_home: float         # Elo expected score (used by the optimizer's log-loss)
    actual: float           # 1.0 home win, 0.0 away win, 0.5 draw


@dataclass
class _TeamState:
    elo: float = feat.NEUTRAL_ELO
    last_date: Optional[date] = None
    games_played: int = 0
    # Season-to-date net efficiency (avg point margin), tracked point-in-time.
    season: Optional[int] = None
    season_games: int = 0
    margin_sum: float = 0.0
    net_eff: float = 0.0   # season-to-date net_eff AFTER the team's last game
    roster: float = 0.0    # roster strength as of the team's last game


class _Net:
    """Minimal carrier so build_feature_row reads ``.net_rating`` (point-in-time)."""

    __slots__ = ("net_rating",)

    def __init__(self, net_rating: float):
        self.net_rating = net_rating


def fetch_history(db: Database) -> list[tuple]:
    return db.query(HISTORY_QUERY)


def _expected_home(r_home: float, r_away: float, hfa: float) -> float:
    return 1 / (1 + 10 ** ((r_away - (r_home + hfa)) / 400))


def _mov_multiplier(margin: float, elo_diff_winner: float) -> float:
    """FiveThirtyEight-style margin-of-victory multiplier.

    Larger margins move ratings more, but the denominator damps the effect when
    the winner was already favored (``elo_diff_winner`` > 0), correcting the
    auto-correlation that would otherwise inflate strong teams.
    """
    return float(np.log(abs(margin) + 1.0) * (2.2 / (0.001 * elo_diff_winner + 2.2)))


def _coerce_date(d, fallback_ordinal: int) -> date:
    """Best-effort date for history rows.

    Real ingested rows carry a ``date``/``datetime``/``YYYY-MM-DD`` string. Unit
    tests use opaque tokens like ``"d1"``; for those we synthesize a monotonically
    increasing date from ``fallback_ordinal`` so ordering holds, rest is a steady
    1-day cadence, and the long offseason gap (carryover) never fires.
    """
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    # Non-parseable (test token): steady daily cadence from a fixed epoch.
    return date(2000, 1, 1) + timedelta(days=fallback_ordinal)


def _form(history: deque) -> float:
    """Rolling win-rate over the team's recent results, neutral when empty."""
    return sum(history) / len(history) if history else feat.NEUTRAL_FORM


def walk_forward(
    results,
    k_factor: float,
    hfa: float,
    *,
    mov_enabled: bool = True,
    carry: float = 0.75,
    gap_days: int = 90,
    form_window: int = 10,
    roster_pit: Optional[dict] = None,
):
    """Replay history; return ``(list[FeatureRow], dict[str, TeamSnapshot])``.

    The ``net_rating_diff`` feature is computed **point-in-time** here — each
    team's season-to-date average point margin from its prior games this season.
    The ``player_strength_diff`` feature comes from ``roster_pit`` (keyed by
    ``(normalized_team, date_iso)``), the precomputed season-to-date roster
    strength; ``None`` during optimization (-> 0, leaving the Elo log-loss path
    unchanged). Both reset at a season boundary (no prior games).
    """
    from ..matching import normalize_team

    roster_pit = roster_pit or {}
    states: dict[str, _TeamState] = {}
    form_hist: dict[str, deque] = {}
    rows: list[FeatureRow] = []

    def state(team: str) -> _TeamState:
        return states.setdefault(team, _TeamState())

    def hist(team: str) -> deque:
        return form_hist.setdefault(team, deque(maxlen=form_window))

    def roster_for(team: str, current: date) -> float:
        return roster_pit.get((normalize_team(team), current.isoformat()), 0.0)

    def net_eff_pregame(s: _TeamState, season: int) -> float:
        # Season-to-date avg margin using only this season's prior games.
        if s.season != season or s.season_games == 0:
            return 0.0
        return s.margin_sum / s.season_games

    for i, (raw_date, home, away, hs, as_) in enumerate(results):
        current = _coerce_date(raw_date, i)
        season = feat.season_of(current)
        sh, sa = state(home), state(away)

        # Season carryover: regress toward 1500 after a long gap.
        for s in (sh, sa):
            if s.last_date is not None and (current - s.last_date).days > gap_days:
                s.elo = feat.NEUTRAL_ELO + carry * (s.elo - feat.NEUTRAL_ELO)

        h_net, a_net = net_eff_pregame(sh, season), net_eff_pregame(sa, season)
        h_roster, a_roster = roster_for(home, current), roster_for(away, current)
        home_snap = TeamSnapshot(sh.elo, sh.last_date, _form(hist(home)), sh.games_played)
        away_snap = TeamSnapshot(sa.elo, sa.last_date, _form(hist(away)), sa.games_played)

        exp_home = _expected_home(sh.elo, sa.elo, hfa)
        row = feat.build_feature_row(
            home_snap, away_snap, current, hfa,
            _Net(h_net), _Net(a_net), h_roster, a_roster,
        )

        if hs > as_:
            actual = 1.0
        elif hs < as_:
            actual = 0.0
        else:
            actual = 0.5
        rows.append(FeatureRow(row, exp_home, actual))

        # Rating update (with MOV multiplier).
        mult = 1.0
        if mov_enabled and actual != 0.5:
            winner_diff = (sh.elo - sa.elo) if actual == 1.0 else (sa.elo - sh.elo)
            mult = _mov_multiplier(hs - as_, winner_diff)
        shift = k_factor * mult * (actual - exp_home)
        sh.elo += shift
        sa.elo -= shift

        # Advance per-team state (incl. season-to-date margin, reset on new season).
        margin = hs - as_
        for team, s, won, signed in ((home, sh, actual, margin), (away, sa, 1.0 - actual, -margin)):
            s.last_date = current
            s.games_played += 1
            if s.season != season:
                s.season, s.season_games, s.margin_sum = season, 0, 0.0
            s.season_games += 1
            s.margin_sum += signed
            s.net_eff = s.margin_sum / s.season_games
            s.roster = roster_for(team, current)
            hist(team).append(1.0 if won == 1.0 else (0.5 if actual == 0.5 else 0.0))

    snapshots = {
        team: TeamSnapshot(s.elo, s.last_date, _form(form_hist.get(team, deque())),
                           s.games_played, s.net_eff, s.roster, s.season)
        for team, s in states.items()
    }
    return rows, snapshots


def mean_log_loss(results, k_factor: float, hfa: float) -> float:
    """Mean binary cross-entropy of the Elo expectation (the optimizer target)."""
    rows, _ = walk_forward(results, k_factor, hfa)
    if not rows:
        return 1.0
    total = 0.0
    for row in rows:
        p = max(min(row.exp_home, 0.999), 0.001)
        total += -(row.actual * np.log(p) + (1 - row.actual) * np.log(1 - p))
    return total / len(rows)
