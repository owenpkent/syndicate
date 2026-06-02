"""Ingest international football results -> data/wc.duckdb (free, no key).

Source: the martj42 "international football results 1872–present" dataset (a single
public CSV, ~49k matches incl. neutral-site flag and tournament). National-team Elo
trains on ALL internationals; the World Cup model evaluates on the FIFA World Cup
subset. Future fixtures (NA scores — e.g. the 2026 WC) are kept as `completed=False`
so we can predict them.

    python research/wc/ingest_intl.py
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import duckdb
import requests

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "src"))
from sportsball.logging_conf import get_logger  # noqa: E402

log = get_logger("ingest_intl")
CSV_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
DEFAULT_DB = str(REPO / "data" / "wc.duckdb")


def _int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def main() -> None:
    r = requests.get(CSV_URL, timeout=40)
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    out = []
    for x in rows:
        hs, as_ = _int(x["home_score"]), _int(x["away_score"])
        out.append((x["date"], x["home_team"], x["away_team"], hs, as_,
                    x["tournament"], x["neutral"].strip().upper() == "TRUE",
                    hs is not None and as_ is not None))

    con = duckdb.connect(DEFAULT_DB)
    con.execute("DROP TABLE IF EXISTS matches")
    con.execute("""CREATE TABLE matches (
        match_date DATE, home_team TEXT, away_team TEXT,
        home_score INTEGER, away_score INTEGER, tournament TEXT,
        neutral BOOLEAN, completed BOOLEAN);""")
    con.executemany("INSERT INTO matches VALUES (?,?,?,?,?,?,?,?)", out)
    n, done = con.execute("SELECT count(*), count(*) FILTER (WHERE completed) FROM matches").fetchone()
    wc = con.execute("SELECT count(*) FROM matches WHERE tournament='FIFA World Cup'").fetchone()[0]
    upcoming = con.execute("SELECT count(*) FROM matches WHERE NOT completed").fetchone()[0]
    con.close()
    log.info("ingested %d internationals (%d completed, %d upcoming); %d are FIFA World Cup.",
             n, done, upcoming, wc)


if __name__ == "__main__":
    main()
