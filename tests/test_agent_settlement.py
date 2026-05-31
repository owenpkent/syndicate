"""Settlement grading + the exposure reaper."""
from sportsball.agents.settlement import settle_once

from fakes import FakeBroker, FakeDB

# row shape: (id, market_id, home_team, away_team, home_score, away_score)
HOME_WIN = (1, "MOCK-E1-Celtics", "Celtics", "Lakers", 110, 100)
AWAY_BET_LOSS = (2, "MOCK-E1-Lakers", "Celtics", "Lakers", 110, 100)


def test_grades_home_winner_and_reaps_exposure():
    db = FakeDB(available=True, rows=[HOME_WIN])
    broker = FakeBroker()
    broker.set_exposure("MOCK-E1-Celtics", 0.05)

    n = settle_once(db, broker)

    assert n == 1
    update = db.executed[0]
    assert "UPDATE trade_history" in update[0]
    assert update[1] == ("WIN", 1)
    assert "MOCK-E1-Celtics" in broker.cleared  # reaper freed the slot
    assert broker._exposure == {}


def test_grades_losing_side():
    db = FakeDB(available=True, rows=[AWAY_BET_LOSS])
    settle_once(db, FakeBroker())
    assert db.executed[0][1] == ("LOSS", 2)


def test_no_pending_is_noop():
    db = FakeDB(available=True, rows=[])
    assert settle_once(db, FakeBroker()) == 0
    assert db.executed == []


def test_db_unavailable_is_safe():
    assert settle_once(FakeDB(available=False), FakeBroker()) == 0
