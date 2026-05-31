"""The shared win-probability feature contract.

This module is the single source of truth for the model's feature vector. It is
**pure** (no DB/network/sklearn) so the exact same code builds features during
*training* (where we have full forward history) and at *serving* time (where we
have only a persisted per-team snapshot + the current game date). That symmetry
is the whole point: train and serve cannot drift because they call one function.

A :class:`TeamSnapshot` captures everything about a team that the serve path
can't recompute from scratch — its Elo, the date of its last game (for rest /
back-to-back), a rolling win-rate (form), and games played. Training persists
these to ``models/team_state.json``; serving loads them back.

Every feature is a **difference of per-team quantities**, and every missing
input degrades to a neutral ``0`` contribution, so a cold-start team or an
absent stat never crashes and never silently biases a side.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

# Bump SCHEMA_VERSION whenever FEATURE_ORDER changes; ModelBundle.load refuses to
# serve an artifact whose schema doesn't match, forcing a retrain (never a
# wrong-width predict).
SCHEMA_VERSION = 3

FEATURE_ORDER = [
    "elo_diff_hfa",          # (home.elo + hfa) - away.elo
    "net_rating_diff",       # home net rating - away net rating
    "rest_diff",             # home rest days - away rest days
    "b2b_home",              # home on a back-to-back?
    "b2b_away",              # away on a back-to-back?
    "form_diff",             # home rolling win% - away rolling win%
    "player_strength_diff",  # home roster strength - away roster strength
    "availability_diff",     # home roster availability - away (point-in-time)
]
N_FEATURES = len(FEATURE_ORDER)

NEUTRAL_ELO = 1500.0
NEUTRAL_FORM = 0.5
MAX_REST_DAYS = 10.0  # cap so an off-season gap doesn't dominate the feature
DEFAULT_TEMPERATURE = 1.0  # post-hoc confidence scaling; >1 shrinks toward 0.5


def temperature_scale(p: float, temperature: float = DEFAULT_TEMPERATURE) -> float:
    """Apply temperature scaling to a probability (T=1 is identity).

    The raw logistic is systematically over-confident out-of-sample; dividing the
    logit by ``T > 1`` pulls predictions toward 0.5, fixing calibration without
    changing the ranking. ``T`` is fit at train time on a held-out tail.
    """
    if temperature == 1.0 or temperature <= 0:
        return p
    p = min(max(p, 1e-9), 1 - 1e-9)
    logit = math.log(p / (1 - p))
    return 1.0 / (1.0 + math.exp(-logit / temperature))


def season_of(d: date) -> int:
    """League-season key for a date. Games from August on belong to that year's
    season (so a season spanning Oct–Jun maps to a single key)."""
    return d.year if d.month >= 8 else d.year - 1


@dataclass
class TeamSnapshot:
    """A team's persisted state, as of the last game in training history.

    ``net_eff`` (season-to-date avg point margin) and ``roster`` (season-to-date
    roster strength) are point-in-time; ``season`` lets the serve path reset them
    to 0 when a new season has started (no prior games yet).
    """

    elo: float = NEUTRAL_ELO
    last_game_date: Optional[date] = None
    form: float = NEUTRAL_FORM
    games_played: int = 0
    net_eff: float = 0.0
    roster: float = 0.0
    season: Optional[int] = None
    # Point-in-time roster availability (share of season-to-date roster strength
    # actually expected to play). 0.0 = unknown/neutral; the serve path overrides
    # this with tonight's injury-adjusted value rather than reusing a stale one.
    availability: float = 0.0


def neutral_snapshot() -> TeamSnapshot:
    """Cold-start default for a team we've never seen (a sane prior)."""
    return TeamSnapshot()


def rest_days(current_date: Optional[date], last_game_date: Optional[date]) -> float:
    """Days since the team's previous game, capped; 0 when either is unknown.

    No prior game (or no current date) → ``0`` so both sides contribute the same
    neutral value and ``rest_diff`` is unaffected.
    """
    if current_date is None or last_game_date is None:
        return 0.0
    delta = (current_date - last_game_date).days
    if delta < 0:
        return 0.0
    return float(min(delta, MAX_REST_DAYS))


def is_b2b(current_date: Optional[date], last_game_date: Optional[date]) -> float:
    """1.0 if the team played yesterday (a back-to-back), else 0.0."""
    if current_date is None or last_game_date is None:
        return 0.0
    return 1.0 if (current_date - last_game_date).days == 1 else 0.0


def _net(stat) -> float:
    """net_rating from a TeamStat-like object, or 0.0 if absent."""
    return float(getattr(stat, "net_rating", 0.0)) if stat is not None else 0.0


def build_feature_row(
    home_snap: TeamSnapshot,
    away_snap: TeamSnapshot,
    current_date: Optional[date],
    hfa: float,
    home_stat=None,
    away_stat=None,
    home_player_strength: Optional[float] = None,
    away_player_strength: Optional[float] = None,
    home_availability: Optional[float] = None,
    away_availability: Optional[float] = None,
) -> list[float]:
    """Build the model feature vector in ``FEATURE_ORDER``.

    Called identically by training and serving. ``home_stat``/``away_stat`` are
    optional TeamStat-like objects (``net_rating``); player strength and
    availability are passed separately so train and serve can source them the same
    way. Any missing input contributes a neutral ``0`` — so with no availability
    data the ``availability_diff`` feature is inert and the model behaves exactly
    as it did before the feature existed.
    """
    home_rest = rest_days(current_date, home_snap.last_game_date)
    away_rest = rest_days(current_date, away_snap.last_game_date)
    hps = float(home_player_strength) if home_player_strength is not None else 0.0
    aps = float(away_player_strength) if away_player_strength is not None else 0.0
    hav = float(home_availability) if home_availability is not None else 0.0
    aav = float(away_availability) if away_availability is not None else 0.0

    row = {
        "elo_diff_hfa": (home_snap.elo + hfa) - away_snap.elo,
        "net_rating_diff": _net(home_stat) - _net(away_stat),
        "rest_diff": home_rest - away_rest,
        "b2b_home": is_b2b(current_date, home_snap.last_game_date),
        "b2b_away": is_b2b(current_date, away_snap.last_game_date),
        "form_diff": home_snap.form - away_snap.form,
        "player_strength_diff": hps - aps,
        "availability_diff": hav - aav,
    }
    return [float(row[name]) for name in FEATURE_ORDER]
