"""Oracle signal construction and Scout order-book parsing."""
from sportsball.agents.oracle import build_signal, fetch_mock_lines
from sportsball.agents.scout import parse_book


class TestOracle:
    def test_build_signal_schema(self):
        sig = build_signal("RUNDOWN", "777", "Lakers", "Celtics", "Celtics", 1.91, "19")
        assert sig["market_id"] == "RUNDOWN-777-Celtics"
        assert sig["odds"] == 1.91
        assert sig["metadata"]["matchup"] == "Lakers @ Celtics"
        assert "true_prob" not in sig  # Oracle never invents probability

    def test_mock_lines_emit_both_sides_per_game(self):
        signals = fetch_mock_lines()
        events = {s["market_id"].rsplit("-", 1)[0] for s in signals}
        # Two games, both sides each -> 4 signals across 2 events.
        assert len(signals) == 4
        assert len(events) == 2

    def test_mock_lines_are_deterministic(self):
        assert fetch_mock_lines() == fetch_mock_lines()


class TestScout:
    def test_parses_valid_book(self):
        sig = parse_book({"type": "book", "asset_id": "abc",
                          "bids": [["0.40"]], "asks": [["0.60"]]})
        assert sig["market_id"] == "POLY-abc"
        assert sig["odds"] == 2.0  # mid 0.50 -> 1/0.50
        assert sig["metadata"]["mid_implied_prob"] == 0.5

    def test_ignores_non_book_messages(self):
        assert parse_book({"type": "trade"}) is None

    def test_ignores_empty_book(self):
        assert parse_book({"type": "book", "asset_id": "x", "bids": [], "asks": []}) is None
