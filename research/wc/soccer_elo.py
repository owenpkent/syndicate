"""International-football Elo — neutral-aware, goal-difference weighted, point-in-time.

National teams play sporadically over 150 years, so this is a continuous Elo (no
seasons), with the World-Football-Elo goal-difference multiplier and home advantage
applied ONLY to non-neutral matches (the World Cup is mostly neutral ground). One
chronological pass yields each match's pre-match Elo diff (the model input) without
look-ahead; upcoming fixtures get a pre-match diff but don't update ratings.
"""
from __future__ import annotations

from collections import defaultdict


def _gd_mult(gd: int) -> float:
    """World Football Elo goal-difference multiplier."""
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return 1.75 + (gd - 3) / 8.0


def run_elo(matches, k: float = 40.0, hfa: float = 65.0):
    """``matches``: ``(home, away, home_score, away_score, neutral, completed)`` in
    chronological order. Returns ``(feats, ratings)`` where ``feats[i]`` is
    ``(elo_diff_pre, neutral, label, completed)`` — ``label`` ∈ {'W','D','L'} (home
    perspective) for completed matches else ``None`` — and ``ratings`` is the final
    per-team Elo. ``elo_diff_pre`` already folds in home advantage on non-neutral
    games, so it is the single model input."""
    R: dict = defaultdict(lambda: 1500.0)
    feats = []
    for home, away, hs, as_, neutral, completed in matches:
        diff = R[home] + (0.0 if neutral else hfa) - R[away]
        label = None
        if completed:
            res = 1.0 if hs > as_ else (0.0 if hs < as_ else 0.5)
            label = "W" if hs > as_ else ("L" if hs < as_ else "D")
            exp = 1.0 / (1.0 + 10 ** (-diff / 400.0))
            shift = k * _gd_mult(abs(hs - as_)) * (res - exp)
            R[home] += shift; R[away] -= shift
        feats.append((diff, bool(neutral), label, bool(completed)))
    return feats, R


if __name__ == "__main__":  # self-check: a dominant team's rating should rise
    m = [("A", "B", 3, 0, True, True), ("A", "B", 2, 0, True, True), ("A", "C", 1, 0, True, True)]
    feats, R = run_elo(m)
    assert R["A"] > 1500 > R["B"], R
    assert feats[1][0] > feats[0][0], feats  # A's edge grows after a win
    print("self-check OK:", {t: round(v, 1) for t, v in R.items()})
