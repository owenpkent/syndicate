"""Settlement grading, PnL, and the exposure reaper on the FK-joined store."""
import pytest

from sportsball.agents.settlement import grade, settle_once
from sportsball.store import PendingTrade, Store

from fakes import FakeBroker, FakeDB

# pending_settlements row order: (id, side, executed_odds, stake_frac, market_id, home_score, away_score)
HOME_WIN = (1, "HOME", 2.0, 0.05, "MOCK-E1-Celtics", 110, 100)
HOME_LOSS = (2, "HOME", 2.0, 0.05, "MOCK-E1-Celtics", 100, 110)


class TestGrade:
    def test_winning_home_pnl(self):
        status, pnl = grade(PendingTrade(1, "HOME", 2.0, 0.05, "m", 110, 100))
        assert status == "WIN"
        assert pnl == pytest.approx(0.05)  # 0.05 * (2.0 - 1)

    def test_losing_pnl_is_negative_stake(self):
        status, pnl = grade(PendingTrade(1, "AWAY", 2.0, 0.05, "m", 110, 100))
        assert status == "LOSS"
        assert pnl == pytest.approx(-0.05)


class TestSettleOnce:
    def test_settles_and_reaps_exposure(self):
        store = Store(FakeDB(available=True, rows=[HOME_WIN]))
        broker = FakeBroker()
        broker.set_exposure("MOCK-E1-Celtics", 0.05)

        n = settle_once(store, broker)

        assert n == 1
        update_sql, params = store.db.executed[0]
        assert "UPDATE trades" in update_sql
        assert params[0] == "WIN" and params[2] == 1  # (status, pnl, id)
        assert "MOCK-E1-Celtics" in broker.cleared
        assert broker._exposure == {}

    def test_no_pending_is_noop(self):
        store = Store(FakeDB(available=True, rows=[]))
        assert settle_once(store, FakeBroker()) == 0

    def test_db_unavailable_is_safe(self):
        assert settle_once(Store(FakeDB(available=False)), FakeBroker()) == 0
