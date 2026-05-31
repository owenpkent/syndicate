"""Oracle signal construction and Scout order-book parsing."""
from sportsball.agents.oracle import build_signal, fetch_mock_lines
from sportsball.agents.scout import parse_book


class TestOracle:
    def test_build_signal_uses_canonical_event_id(self):
        sig = build_signal("RUNDOWN", "nba", "2024-01-15", "Lakers", "Celtics", "Celtics", 1.91, "19")
        assert sig["market_id"] == "RUNDOWN-nba_20240115_lakers_at_celtics-Celtics"
        assert sig["odds"] == 1.91
        assert sig["metadata"]["matchup"] == "Lakers @ Celtics"
        assert sig["metadata"]["event_id"] == "nba_20240115_lakers_at_celtics"
        assert "true_prob" not in sig  # Oracle never invents probability

    def test_mock_lines_emit_both_sides_per_game(self):
        signals = fetch_mock_lines()
        events = {s["metadata"]["event_id"] for s in signals}
        assert len(signals) == 4
        assert len(events) == 2

    def test_mock_lines_are_deterministic(self):
        assert fetch_mock_lines() == fetch_mock_lines()


class TestScout:
    def test_parses_valid_book(self):
        sig = parse_book({
            "event_type": "book", "asset_id": "abc",
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.60", "size": "10"}],
        }, labels={"abc": ("lakers-vs-celtics", "Yes")})
        assert sig["market_id"] == "POLY-abc-Yes"
        assert sig["odds"] == 2.0  # mid 0.50 -> 1/0.50
        assert sig["metadata"]["slug"] == "lakers-vs-celtics"

    def test_best_bid_ask_selected_from_levels(self):
        sig = parse_book({
            "event_type": "book", "asset_id": "x",
            "bids": [{"price": "0.30", "size": "5"}, {"price": "0.45", "size": "5"}],
            "asks": [{"price": "0.70", "size": "5"}, {"price": "0.55", "size": "5"}],
        })
        # best bid 0.45, best ask 0.55 -> mid 0.50
        assert sig["metadata"]["mid_implied_prob"] == 0.5

    def test_ignores_non_book_and_empty(self):
        assert parse_book({"event_type": "price_change"}) is None
        assert parse_book({"event_type": "book", "asset_id": "x", "bids": [], "asks": []}) is None
