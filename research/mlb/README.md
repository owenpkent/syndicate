# research/mlb — baseball win-probability model

The project's model pipeline (`walk_forward` Elo + the shared feature builder) is
**sport-agnostic**, but `events` holds only NBA. This adds the missing piece —
real MLB results — so the *same* machinery trains and measures a baseball model.

| File | What |
|---|---|
| `ingest_mlb.py` | Regular-season finals + **starting pitchers** from the free **MLB Stats API** (statsapi.mlb.com, no key) → `data/mlb.duckdb` table `games` (one row per `game_pk`; upserts, so re-running refreshes the live season). `python research/mlb/ingest_mlb.py --start 2010 --end 2026`. |
| `pitcher_features.py` | Pure, leakage-free **starting-pitcher run-prevention** rating (point-in-time rolling mean of opponent runs per start). Appended to the model matrix in the notebook — kept out of the shared 9-feature `quant/features.py` contract. |
| `../notebooks/07_mlb_model.ipynb` | Train the Elo+feature pipeline (+pitcher) on the games; holdout accuracy/log-loss/calibration, Elo ratings, best-run-prevention starters, skill-over-time. |

## Honest result (2010–2026, ~38k games)

Modest but **real** skill: ~56% accuracy / log-loss 0.681 out-of-sample, vs
always-pick-home 0.532 / 0.691. Baseball is high-variance, and team-Elo is blind to
the **starting pitcher** — the dominant per-game lever.

**Pitcher feature, honest verdict:** the run-prevention *proxy* (opponent runs when
a starter pitched) has some signal alone (beats always-home) but adds only ~−0.0007
log-loss on top of team-Elo — **marginal / within noise**, because the proxy folds
in bullpen/defense/park and barely isolates the pitcher. The real unlock is a true
pitcher rating (FIP / K-BB% / xERA from per-start pitcher game logs), a heavier
per-pitcher fetch. Ingested closing odds (`market_logit`) would also help. Elo
params: K=4, HFA=24 (538-style, low for baseball).
