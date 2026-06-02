"""Per-start pitcher game logs -> data/mlb.duckdb `pitcher_logs` (free, no key).

The real starting-pitcher signal (vs the run-prevention proxy): pull each
starter's per-game pitching line so we can build a point-in-time **FIP** rating
that isolates the pitcher (K, BB, HR, IP) from his team/bullpen/defense.

Fetches one `gameLog` per (starter, season) that appears in `games` — ~5.5k calls,
so it runs a few minutes. Idempotent (`ON CONFLICT DO NOTHING`).

    python research/mlb/ingest_pitcher_logs.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))
from sportsball.logging_conf import get_logger  # noqa: E402

log = get_logger("ingest_pitcher_logs")
LOG_URL = "https://statsapi.mlb.com/api/v1/people/{pid}/stats"
DEFAULT_DB = str(REPO / "data" / "mlb.duckdb")


def ip_to_outs(ip: str) -> int:
    """'6.1' -> 19 outs (6 innings + 1 out). '6.0' -> 18, '6.2' -> 20."""
    try:
        whole, _, frac = str(ip).partition(".")
        return int(whole) * 3 + int(frac or 0)
    except Exception:  # noqa: BLE001
        return 0


def fetch_starts(pid: int, season: int) -> list[tuple]:
    r = requests.get(LOG_URL.format(pid=pid), params={
        "stats": "gameLog", "group": "pitching", "season": season}, timeout=25)
    r.raise_for_status()
    stats = r.json().get("stats", [])
    out = []
    for sp in (stats[0]["splits"] if stats else []):
        s = sp.get("stat", {})
        if not s.get("gamesStarted"):           # starts only
            continue
        gpk = sp.get("game", {}).get("gamePk")
        if gpk is None:
            continue
        out.append((pid, gpk, sp.get("date"), season, ip_to_outs(s.get("inningsPitched", "0")),
                    int(s.get("strikeOuts") or 0), int(s.get("baseOnBalls") or 0),
                    int(s.get("homeRuns") or 0), int(s.get("hitByPitch") or 0),
                    int(s.get("battersFaced") or 0), int(s.get("earnedRuns") or 0)))
    return out


def main() -> None:
    con = duckdb.connect(DEFAULT_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS pitcher_logs (
        pitcher_id BIGINT, game_pk BIGINT, game_date DATE, season INTEGER,
        outs INTEGER, so INTEGER, bb INTEGER, hr INTEGER, hbp INTEGER,
        bf INTEGER, er INTEGER, PRIMARY KEY (pitcher_id, game_pk));""")
    pairs = con.execute("""
        SELECT DISTINCT pid, season FROM (
          SELECT home_sp_id AS pid, season FROM games WHERE home_sp_id IS NOT NULL
          UNION SELECT away_sp_id, season FROM games WHERE away_sp_id IS NOT NULL)
        ORDER BY season, pid""").fetchall()
    log.info("fetching %d (pitcher, season) logs...", len(pairs))
    total = 0
    for i, (pid, season) in enumerate(pairs, 1):
        try:
            rows = fetch_starts(pid, season)
        except Exception as exc:  # noqa: BLE001
            log.warning("pid %s season %s failed: %s", pid, season, exc); continue
        if rows:
            con.executemany("INSERT INTO pitcher_logs VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT DO NOTHING;", rows)
            total += len(rows)
        if i % 500 == 0:
            log.info("  %d/%d pairs, %d start-logs so far", i, len(pairs), total)
        time.sleep(0.2)
    n = con.execute("SELECT count(*) FROM pitcher_logs").fetchone()[0]
    con.close()
    log.info("done. %d start-logs in pitcher_logs (this run added ~%d).", n, total)


if __name__ == "__main__":
    main()
