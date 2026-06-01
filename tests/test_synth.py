"""The synthetic-season generator used by the offline measurement/dry-run tools."""
import numpy as np

from synth import make_season


def test_two_tuple_by_default():
    out = make_season(np.random.default_rng(0), n_teams=8, n_games=200)
    assert len(out) == 2
    results, avail = out
    assert len(results) == 200
    assert all(len(r) == 5 for r in results)            # (date, home, away, hs, as)
    assert all(0.0 <= v <= 1.0 for v in avail.values())  # availability in [0,1]


def test_with_market_returns_keyed_probabilities():
    results, avail, market = make_season(
        np.random.default_rng(1), n_teams=8, n_games=200, with_market=True)
    assert market, "market_pit should be populated"
    # keyed by (home_token, away_token, date_iso); probs are valid
    k = next(iter(market))
    assert len(k) == 3
    assert all(0.0 < p < 1.0 for p in market.values())


def test_market_tracks_outcomes():
    # A higher market P(home) should coincide with more home wins (it's an
    # efficient estimate of the true prob by construction).
    results, _, market = make_season(
        np.random.default_rng(2), n_teams=10, n_games=3000, with_market=True)
    from sportsball.matching import normalize_team
    hi, lo = [], []
    for d, home, away, hs, as_ in results:
        p = market.get((normalize_team(home), normalize_team(away), d.isoformat()))
        if p is None:
            continue
        (hi if p >= 0.5 else lo).append(1 if hs > as_ else 0)
    assert np.mean(hi) > np.mean(lo)
