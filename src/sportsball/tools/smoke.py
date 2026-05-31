"""Live-integration smoke test.

Hits the *real* external services the pipeline depends on and reports what comes
back, so the integrations can be validated end to end (they are otherwise only
unit-tested against documented shapes). Each check is isolated — one failure
doesn't abort the others — and the process exits non-zero if any check fails.

    make smoke                     # gamma + nba (one season) + a short WS probe
    sportsball-smoke --skip-ws     # skip the order-book probe
    sportsball-smoke --nba-season 2023-24 --ws-timeout 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ..agents.scout import CLOB_WS_URL, parse_book
from ..logging_conf import get_logger
from ..markets.polymarket import fetch_markets, token_map
from ..pipelines.ingest_nba import build_games

log = get_logger("smoke")


def check_gamma(limit: int = 5):
    """Returns (ok, markets) — fetches active Polymarket markets via Gamma."""
    print("\n[Gamma API] fetching active markets...")
    markets = fetch_markets(limit=limit)
    if not markets:
        print("  FAIL: no markets returned (network/endpoint issue?)")
        return False, []
    print(f"  OK: {len(markets)} markets, {sum(len(m.token_ids) for m in markets)} tokens")
    for m in markets[:3]:
        print(f"    - {m.slug or '(no slug)'}: outcomes={m.outcomes} tokens={len(m.token_ids)}")
    return True, markets


def check_nba(season: str):
    """Returns ok — fetches one NBA season from nba_api and pairs games."""
    print(f"\n[nba_api] fetching {season} regular season...")
    try:
        from nba_api.stats.endpoints import leaguegamelog
        df = leaguegamelog.LeagueGameLog(
            season=season, season_type_all_star="Regular Season").get_data_frames()[0]
        games = build_games(df.to_dict("records"))
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: {exc}")
        return False
    if not games:
        print("  FAIL: no games parsed")
        return False
    g = games[0]
    print(f"  OK: {len(games)} games. Sample: {g.event_id} "
          f"({g.away_team} {g.away_score} @ {g.home_team} {g.home_score})")
    return True


async def _ws_probe(token: str, timeout: float):
    import websockets
    async with websockets.connect(CLOB_WS_URL) as ws:
        await ws.send(json.dumps({"type": "market", "assets_ids": [token]}))
        seen, signal = set(), None
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - loop.time()))
            payload = json.loads(raw)
            for item in (payload if isinstance(payload, list) else [payload]):
                seen.add(item.get("event_type"))
                signal = signal or parse_book(item)
        return seen, signal


def check_ws(markets, timeout: float):
    """Returns ok — connects to the CLOB market channel and awaits a book message."""
    print(f"\n[CLOB WebSocket] probing {CLOB_WS_URL} (up to {timeout}s)...")
    token = next((t for m in markets for t in m.token_ids), None)
    if not token:
        print("  SKIP: no token from discovery to subscribe to")
        return True
    try:
        seen, signal = asyncio.run(_ws_probe(token, timeout))
    except asyncio.TimeoutError:
        print("  WARN: connected but no message before timeout (market may be quiet)")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: {exc}")
        return False
    print(f"  OK: connected, event_types seen: {sorted(s for s in seen if s)}")
    if signal:
        print(f"    parsed book -> odds {signal['odds']} "
              f"(mid {signal['metadata']['mid_implied_prob']})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Live-integration smoke test")
    parser.add_argument("--nba-season", default="2024-25")
    parser.add_argument("--ws-timeout", type=float, default=12.0)
    parser.add_argument("--skip-ws", action="store_true")
    parser.add_argument("--skip-nba", action="store_true")
    args = parser.parse_args()

    print("=== Sportsball live-integration smoke test ===")
    results = {}
    gamma_ok, markets = check_gamma()
    results["gamma"] = gamma_ok
    if not args.skip_nba:
        results["nba"] = check_nba(args.nba_season)
    if not args.skip_ws:
        results["websocket"] = check_ws(markets, args.ws_timeout)

    print("\n=== Summary ===")
    for name, ok in results.items():
        print(f"  {name:<12} {'OK' if ok else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
