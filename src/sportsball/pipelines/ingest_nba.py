"""Ingest real NBA game results from nba_api — free, no API key.

The Rundown backfill needs a paid key and only covers a key-holder's window.
``stats.nba.com`` (via ``nba_api``) exposes every regular-season game's final
score for free, which is exactly the training signal the Elo + logistic model
needs (scores, not odds). This is the recommended way to get "more data" behind
the model.

Loaded games are written to ``events`` as FINAL with a canonical ``event_id``
(so they align with Rundown/Polymarket signals for the same game) but with NULL
closing odds — CLV/odds-backtests need a price source; model training does not.
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict
from dataclasses import dataclass

from ..config import load_settings
from ..db import Database
from ..logging_conf import get_logger
from ..matching import canonical_event_id
from ..store import Store

log = get_logger("ingest_nba")

NBA_SPORT_ID = 4
DEFAULT_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25"]


@dataclass
class Game:
    event_id: str
    game_date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int


def build_games(rows: list[dict]) -> list[Game]:
    """Pair the two team-rows per GAME_ID into one Game (pure; nba_api-free).

    Each nba_api game-log row is one team's line; ``MATCHUP`` is "LAL vs. BOS"
    for the home team and "LAL @ BOS" for the away team.
    """
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[r.get("GAME_ID")].append(r)

    games: list[Game] = []
    for pair in by_game.values():
        if len(pair) != 2:
            continue
        home = next((r for r in pair if "vs." in (r.get("MATCHUP") or "")), None)
        away = next((r for r in pair if "@" in (r.get("MATCHUP") or "")), None)
        if not home or not away or home.get("PTS") is None or away.get("PTS") is None:
            continue
        eid = canonical_event_id("nba", home["GAME_DATE"], away["TEAM_NAME"], home["TEAM_NAME"])
        games.append(Game(eid, str(home["GAME_DATE"]), home["TEAM_NAME"], away["TEAM_NAME"],
                          int(home["PTS"]), int(away["PTS"])))
    return games


def ingest(store: Store, seasons: list[str]) -> int:
    from nba_api.stats.endpoints import leaguegamelog  # lazy: host-only dependency

    total = 0
    for season in seasons:
        log.info("Fetching NBA regular-season games for %s...", season)
        df = leaguegamelog.LeagueGameLog(
            season=season, season_type_all_star="Regular Season").get_data_frames()[0]
        games = build_games(df.to_dict("records"))
        for g in games:
            store.upsert_event_result(g.event_id, NBA_SPORT_ID, g.game_date,
                                      g.home_team, g.away_team, g.home_score, g.away_score, 0, 0)
        log.info("%s: ingested %d games", season, len(games))
        total += len(games)
        time.sleep(1)  # be polite to stats.nba.com
    log.info("Done. Ingested %d games across %d seasons.", total, len(seasons))
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NBA results from nba_api (free, no key)")
    parser.add_argument("--seasons", default=",".join(DEFAULT_SEASONS),
                        help="comma-separated, e.g. 2022-23,2023-24")
    args = parser.parse_args()
    store = Store(Database(load_settings().db))
    if not store.available:
        log.error("Database unavailable.")
        return
    ingest(store, [s.strip() for s in args.seasons.split(",") if s.strip()])


if __name__ == "__main__":
    main()
