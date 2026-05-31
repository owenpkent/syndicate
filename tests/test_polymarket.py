"""Polymarket Gamma discovery parsing (pure; no network)."""
from sportsball.markets.polymarket import (
    parse_game_market,
    parse_markets,
    token_map,
    token_meta,
)

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


# Real Gamma shapes captured from the live API.
def _mkt(slug, outcomes, tokens=("a", "b")):
    from sportsball.markets.polymarket import PolyMarket
    return PolyMarket(slug=slug, question="", outcomes=list(outcomes), token_ids=list(tokens))


class TestParseGameMarket:
    def test_head_to_head_with_date(self):
        # cricipl-roy-guj-2026-05-31 / ["Royal Challengers Bengaluru","Gujarat Titans"]
        gm = parse_game_market(_mkt("cricipl-roy-guj-2026-05-31",
                                    ["Royal Challengers Bengaluru", "Gujarat Titans"]))
        assert gm is not None
        assert gm.date == "2026-05-31"
        assert gm.away == "Royal Challengers Bengaluru" and gm.home == "Gujarat Titans"
        assert gm.matchup == "Royal Challengers Bengaluru @ Gujarat Titans"
        assert "2026" in gm.event_id

    def test_nba_maps_sport_and_aligns_event_id(self):
        from sportsball.matching import canonical_event_id
        gm = parse_game_market(_mkt("nba-lal-bos-2024-01-15", ["Lakers", "Celtics"]))
        assert gm.sport == "nba"
        # same canonical id the Oracle would build for away @ home
        assert gm.event_id == canonical_event_id("nba", "2024-01-15", "Lakers", "Celtics")

    def test_yes_no_futures_skipped(self):
        assert parse_game_market(
            _mkt("will-the-knicks-win-the-2026-nba-finals", ["Yes", "No"])) is None

    def test_undated_skipped(self):
        assert parse_game_market(_mkt("lakers-vs-celtics", ["Lakers", "Celtics"])) is None

    def test_three_way_skipped(self):
        assert parse_game_market(_mkt("x-2026-05-31", ["A", "B", "Draw"])) is None


def test_token_meta_shares_gamemeta_across_both_tokens():
    m = _mkt("nba-lal-bos-2024-01-15", ["Lakers", "Celtics"], tokens=("t1", "t2"))
    meta = token_meta([m])
    assert meta["t1"][0] == "Lakers" and meta["t2"][0] == "Celtics"
    assert meta["t1"][1] is meta["t2"][1] is not None  # same GameMeta object
    assert meta["t1"][1].event_id == meta["t2"][1].event_id
