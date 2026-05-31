"""Polymarket Gamma discovery parsing (pure; no network)."""
from sportsball.markets.polymarket import parse_markets, token_map

# Gamma returns clobTokenIds and outcomes as JSON-encoded strings.
RAW = [
    {
        "slug": "lakers-vs-celtics",
        "question": "Will the Lakers win?",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["111", "222"]',
    },
    {"slug": "no-tokens", "question": "x", "outcomes": "[]", "clobTokenIds": "[]"},
]


class TestParseMarkets:
    def test_decodes_json_string_arrays(self):
        markets = parse_markets(RAW)
        assert len(markets) == 1  # the tokenless market is dropped
        m = markets[0]
        assert m.token_ids == ["111", "222"]
        assert m.outcomes == ["Yes", "No"]
        assert m.slug == "lakers-vs-celtics"

    def test_tolerates_real_lists(self):
        markets = parse_markets([{"slug": "s", "clobTokenIds": ["1"], "outcomes": ["Yes"]}])
        assert markets[0].token_ids == ["1"]


def test_token_map_pairs_tokens_to_outcomes():
    markets = parse_markets(RAW)
    mapping = token_map(markets)
    assert mapping["111"] == ("lakers-vs-celtics", "Yes")
    assert mapping["222"] == ("lakers-vs-celtics", "No")
