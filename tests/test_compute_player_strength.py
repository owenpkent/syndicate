"""Pure roster-strength aggregation (no DuckDB / Postgres needed)."""
import pytest

import sys
from pathlib import Path

# scripts/ isn't a package; add it to the path like the ingest tests do.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import compute_player_strength as cps  # noqa: E402


def test_empty_is_zero():
    assert cps.roster_strength([]) == 0.0
    assert cps.roster_strength([{"minutes": 0, "plus_minus": 50}]) == 0.0


def test_positive_plusminus_is_positive():
    rows = [{"minutes": 1000, "plus_minus": 200}, {"minutes": 800, "plus_minus": 120}]
    assert cps.roster_strength(rows) > 0


def test_top_n_limits_contributors():
    # 9 players, but only the top-8 by minutes count. A tiny-minutes superstar is excluded.
    rows = [{"minutes": 1000, "plus_minus": 100} for _ in range(8)]
    rows.append({"minutes": 1, "plus_minus": 10_000})  # huge per-min, but below the cut
    strength = cps.roster_strength(rows, top_n=8)
    only_eight = cps.roster_strength(rows[:8], top_n=8)
    assert strength == pytest.approx(only_eight)


def test_aggregate_by_team_groups():
    rows = [
        {"team_name": "A", "minutes": 500, "plus_minus": 50},
        {"team_name": "A", "minutes": 400, "plus_minus": 20},
        {"team_name": "B", "minutes": 600, "plus_minus": -60},
    ]
    agg = cps.aggregate_by_team(rows)
    assert set(agg) == {"A", "B"}
    assert agg["A"] > 0 > agg["B"]
