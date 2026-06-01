"""Synthetic season generator for offline end-to-end exercises.

Produces a complete, internally-consistent dataset — game results plus a
point-in-time availability signal that genuinely moves outcomes — so the real
modeling code (``walk_forward`` + the shared feature builder + the logistic) can
be run on a full dataset without any network, DB, or DuckDB. Used both by the
availability integration test and ``scripts/offline_dryrun.py``.

The data-generating process is deliberately simple and known:

    margin ~ Normal( RATING*(r_home - r_away) + HFA
                     + AVAIL*(avail_home - avail_away), noise )

so a team that rests its rotation (low availability) is genuinely more likely to
lose by more — exactly the signal the ``availability_diff`` feature should learn.
``avail`` is drawn i.i.d. per game, so the Elo walk cannot absorb it; any lift it
provides is real.
"""
from __future__ import annotations

from datetime import date, timedelta

from sportsball.matching import normalize_team

RATING = 9.0   # points of margin per unit of latent team rating
HFA = 2.8      # home-court margin
AVAIL = 7.0    # points of margin per unit of availability advantage
NOISE = 11.0   # margin noise (sd)


def make_season(rng, n_teams: int = 12, n_games: int = 4000, start="2023-10-20"):
    """Return ``(results, availability_pit)``.

    ``results``: ``[(date, home, away, home_score, away_score)]`` in date order.
    ``availability_pit``: ``{(normalized_team, date_iso): availability}`` — the
    per-game availability each side actually had, the leakage-free signal the
    trainer joins.
    """
    teams = [f"T{i}" for i in range(n_teams)]
    ratings = {t: float(rng.normal(0, 1)) for t in teams}
    y0, m0, d0 = (int(x) for x in start.split("-"))
    day0 = date(y0, m0, d0)

    results = []
    availability_pit: dict = {}
    for g in range(n_games):
        gd = day0 + timedelta(days=g // 6)  # ~6 games a day, dates advance in order
        home, away = rng.choice(teams, size=2, replace=False)
        av_h = float(rng.uniform(0.55, 1.0))
        av_a = float(rng.uniform(0.55, 1.0))
        mu = (RATING * (ratings[home] - ratings[away]) + HFA
              + AVAIL * (av_h - av_a))
        margin = int(round(rng.normal(mu, NOISE)))
        if margin == 0:
            margin = 1  # no ties in basketball
        hs = 100 + max(0, margin)
        as_ = 100 + max(0, -margin)
        results.append((gd, home, away, hs, as_))
        iso = gd.isoformat()
        availability_pit[(normalize_team(home), iso)] = av_h
        availability_pit[(normalize_team(away), iso)] = av_a
    return results, availability_pit
