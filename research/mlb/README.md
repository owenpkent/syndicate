# research/mlb — baseball win-probability model

The project's model pipeline (`walk_forward` Elo + the shared feature builder) is
**sport-agnostic**, but `events` holds only NBA. This adds the missing piece —
real MLB results — so the *same* machinery trains and measures a baseball model.

| File | What |
|---|---|
| `ingest_mlb.py` | Pull regular-season finals from the free **MLB Stats API** (statsapi.mlb.com, no key) → `data/mlb.duckdb` table `games`. One row per `game_pk`. `python research/mlb/ingest_mlb.py --start 2010 --end 2026`. |
| `../notebooks/07_mlb_model.ipynb` | Train the Elo+feature pipeline on the ingested games; out-of-sample holdout (accuracy / log-loss / calibration), final Elo ratings, skill-over-time. |

## Honest result (2010–2026, ~38k games)

Modest but **real** skill: ~56% accuracy / log-loss 0.681 out-of-sample, vs
always-pick-home 0.532 / 0.691. Baseball is high-variance, and the ceiling is the
**starting pitcher** — team-level Elo can't see Ace-vs-swingman, the dominant lever
in an MLB game. That (a starting-pitcher rating/form feature) is the obvious next
unlock; park factors, bullpen, and ingested closing odds (`market_logit`) are
smaller follow-ons. Elo params: K=4, HFA=24 (538-style, low for baseball).
