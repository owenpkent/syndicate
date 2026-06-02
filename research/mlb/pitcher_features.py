"""Point-in-time starting-pitcher rating — the dominant MLB lever.

Team-level Elo can't see that tonight is an ace vs. a swingman. This adds a
leakage-free per-starter run-prevention rating, computed in one chronological
pass over the games (the same order the Elo walk uses), so it lines up row-for-row
with the Elo feature matrix.

Kept OUT of the shared `quant/features.py` (which has a fixed 9-feature contract
the NBA model depends on) — it's an MLB-only research feature, appended to the
model matrix in `notebooks/07_mlb_model.ipynb`.
"""
from __future__ import annotations

from collections import defaultdict, deque


def pitcher_run_prevention(games, window: int = 10, league_default: float = 4.5) -> dict:
    """Rolling run-prevention per starting pitcher, point-in-time.

    ``games``: iterable of ``(home_sp_id, away_sp_id, home_score, away_score)`` in
    chronological order. A starter is charged with the runs the OPPOSING team
    scored in his game (a proxy that folds in the bullpen but tracks pitcher
    quality well). Each rating is the mean over that pitcher's prior ``window``
    starts only — updated AFTER the game, so there is no look-ahead. Debut/unknown
    starts fall back to the running league average.

    Returns equal-length lists keyed ``home_sp_ra`` / ``away_sp_ra`` /
    ``pitcher_adv_home`` (= away_sp_ra − home_sp_ra: positive when the home
    starter prevents runs and the opponent's starter allows them).
    """
    hist: dict = defaultdict(lambda: deque(maxlen=window))
    league: deque = deque(maxlen=4000)
    h_ra, a_ra, adv = [], [], []
    for home_sp, away_sp, hs, as_ in games:
        lavg = sum(league) / len(league) if league else league_default
        hr = sum(hist[home_sp]) / len(hist[home_sp]) if (home_sp is not None and hist[home_sp]) else lavg
        ar = sum(hist[away_sp]) / len(hist[away_sp]) if (away_sp is not None and hist[away_sp]) else lavg
        h_ra.append(hr); a_ra.append(ar); adv.append(ar - hr)
        if home_sp is not None:
            hist[home_sp].append(as_)   # home starter "allowed" the away runs
        if away_sp is not None:
            hist[away_sp].append(hs)
        league.append(as_); league.append(hs)
    return {"home_sp_ra": h_ra, "away_sp_ra": a_ra, "pitcher_adv_home": adv}


if __name__ == "__main__":  # tiny self-check
    # ace (id 1) always shuts out; batting-practice (id 2) always allows 10.
    games = [(1, 2, 5, 0), (1, 2, 6, 0), (2, 1, 0, 10), (1, 2, 4, 0)]
    out = pitcher_run_prevention(games, window=5)
    # by the 3rd game ace(1) home rating should be ~0, opponent(2) ~10 -> strong home adv
    assert out["pitcher_adv_home"][3] > 5, out
    assert out["home_sp_ra"][3] < 1, out
    print("self-check OK:", {k: [round(x, 2) for x in v] for k, v in out.items()})
