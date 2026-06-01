"""Odds conversion, EV, and Kelly sizing."""
import pytest

from sportsball.quant.odds import (
    american_to_decimal,
    calculate_ev,
    calculate_kelly_fraction,
    devig_two_way,
    implied_prob,
)


class TestDevig:
    def test_removes_margin_to_sum_one(self):
        p = devig_two_way(1.90, 1.90)  # symmetric -> 0.5 after de-vig
        assert p == pytest.approx(0.5)

    def test_favorite_above_half(self):
        p = devig_two_way(1.40, 3.00)
        assert 0.5 < p < 1.0

    def test_missing_or_bad_price_is_none(self):
        assert devig_two_way(0, 2.0) is None
        assert devig_two_way(1.0, 2.0) is None
        assert devig_two_way(2.0, None) is None


class TestAmericanToDecimal:
    def test_negative_favorite(self):
        assert american_to_decimal(-110) == pytest.approx(1.9091, abs=1e-4)

    def test_positive_underdog(self):
        assert american_to_decimal(150) == pytest.approx(2.50)

    @pytest.mark.parametrize("sentinel", [0, 0.0001])
    def test_off_board_sentinels_are_zero(self, sentinel):
        assert american_to_decimal(sentinel) == 0.0


class TestImpliedProb:
    def test_even_money(self):
        assert implied_prob(2.0) == pytest.approx(0.5)

    def test_zero_odds_guard(self):
        assert implied_prob(0) == 0.0


class TestExpectedValue:
    def test_break_even(self):
        assert calculate_ev(0.5, 2.0) == pytest.approx(0.0)

    def test_positive_and_negative(self):
        assert calculate_ev(0.6, 2.0) == pytest.approx(0.2)
        assert calculate_ev(0.4, 2.0) == pytest.approx(-0.2)


class TestKelly:
    def test_quarter_scales_full(self):
        full = calculate_kelly_fraction(0.2, 2.0, multiplier=1.0)
        assert calculate_kelly_fraction(0.2, 2.0, multiplier=0.25) == pytest.approx(full * 0.25)

    def test_full_kelly_value(self):
        assert calculate_kelly_fraction(0.2, 2.0, multiplier=1.0) == pytest.approx(0.2)

    def test_negative_ev_stakes_nothing(self):
        assert calculate_kelly_fraction(-0.1, 2.0) == 0

    @pytest.mark.parametrize("odds", [1.0, 0.9])
    def test_no_payout_odds_stake_nothing(self, odds):
        assert calculate_kelly_fraction(0.5, odds) == 0
