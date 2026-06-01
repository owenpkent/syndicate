"""The shared train/serve feature contract."""
from datetime import date

import pytest

from sportsball.quant.features import (
    FEATURE_ORDER,
    N_FEATURES,
    TeamSnapshot,
    build_feature_row,
    is_b2b,
    neutral_snapshot,
    rest_days,
    season_of,
    temperature_scale,
)


class TestSeasonOf:
    def test_august_starts_new_season(self):
        assert season_of(date(2024, 8, 1)) == 2024
        assert season_of(date(2024, 12, 31)) == 2024

    def test_spring_belongs_to_prior_year_season(self):
        assert season_of(date(2025, 1, 15)) == 2024
        assert season_of(date(2025, 6, 1)) == 2024
from sportsball.quant.models import TeamStat


def test_row_length_and_order():
    row = build_feature_row(neutral_snapshot(), neutral_snapshot(), None, 50.0)
    assert len(row) == N_FEATURES == 9
    assert FEATURE_ORDER[0] == "elo_diff_hfa"
    assert FEATURE_ORDER[-1] == "market_logit"


def test_cold_start_is_hfa_only():
    row = build_feature_row(neutral_snapshot(), neutral_snapshot(), None, 50.0)
    assert row[0] == pytest.approx(50.0)  # (1500+50) - 1500
    assert row[1:] == [0.0] * 8           # everything else neutral


def test_rest_and_b2b():
    assert rest_days(None, None) == 0.0
    assert rest_days(date(2024, 1, 10), None) == 0.0
    assert rest_days(date(2024, 1, 10), date(2024, 1, 7)) == 3.0
    assert rest_days(date(2024, 1, 30), date(2024, 1, 1)) == 10.0  # capped
    assert is_b2b(date(2024, 1, 8), date(2024, 1, 7)) == 1.0
    assert is_b2b(date(2024, 1, 9), date(2024, 1, 7)) == 0.0


def test_rest_diff_in_row():
    home = TeamSnapshot(elo=1500, last_game_date=date(2024, 1, 9))   # 1 day rest -> b2b
    away = TeamSnapshot(elo=1500, last_game_date=date(2024, 1, 5))   # 5 days rest
    row = dict(zip(FEATURE_ORDER, build_feature_row(home, away, date(2024, 1, 10), 50.0)))
    assert row["rest_diff"] == pytest.approx(1.0 - 5.0)
    assert row["b2b_home"] == 1.0
    assert row["b2b_away"] == 0.0


def test_missing_stats_contribute_zero():
    row = dict(zip(FEATURE_ORDER, build_feature_row(
        neutral_snapshot(), neutral_snapshot(), None, 50.0,
        home_stat=None, away_stat=None)))
    assert row["net_rating_diff"] == 0.0
    assert row["player_strength_diff"] == 0.0
    assert row["availability_diff"] == 0.0  # no availability data -> inert


def test_availability_diff():
    row = dict(zip(FEATURE_ORDER, build_feature_row(
        neutral_snapshot(), neutral_snapshot(), None, 50.0,
        home_availability=0.9, away_availability=0.4)))
    assert row["availability_diff"] == pytest.approx(0.5)


def test_market_logit_feature():
    import math
    row = dict(zip(FEATURE_ORDER, build_feature_row(
        neutral_snapshot(), neutral_snapshot(), None, 50.0, home_market_prob=0.75)))
    assert row["market_logit"] == pytest.approx(math.log(0.75 / 0.25))
    # unknown market -> neutral 0 (logit of 0.5)
    none_row = dict(zip(FEATURE_ORDER, build_feature_row(
        neutral_snapshot(), neutral_snapshot(), None, 50.0)))
    assert none_row["market_logit"] == 0.0


class TestTemperatureScale:
    def test_identity_at_one(self):
        assert temperature_scale(0.8, 1.0) == 0.8

    def test_midpoint_unchanged(self):
        assert temperature_scale(0.5, 3.0) == pytest.approx(0.5)

    def test_shrinks_toward_half(self):
        # T>1 pulls confident predictions toward 0.5 (both sides).
        assert 0.5 < temperature_scale(0.8, 2.0) < 0.8
        assert 0.2 < temperature_scale(0.2, 2.0) < 0.5

    def test_preserves_ranking(self):
        assert temperature_scale(0.7, 2.0) < temperature_scale(0.75, 2.0)


def test_net_rating_and_player_strength_diff():
    row = dict(zip(FEATURE_ORDER, build_feature_row(
        neutral_snapshot(), neutral_snapshot(), None, 50.0,
        home_stat=TeamStat(net_rating=8, pace=100), away_stat=TeamStat(net_rating=-2, pace=100),
        home_player_strength=0.5, away_player_strength=0.1)))
    assert row["net_rating_diff"] == pytest.approx(10.0)
    assert row["player_strength_diff"] == pytest.approx(0.4)
