# research/nhl — hockey win-probability model

NHL games always have a winner (OT/shootout — no ties since 2005-06), so hockey is a
**binary**, goal-margin sport and reuses the *same* sport-agnostic `walk_forward`
Elo+feature pipeline as NBA/MLB — no new model code.

| File | What |
|---|---|
| `ingest_nhl.py` | Regular-season results from the free **NHL web API** (api-web.nhle.com, no key) → `data/nhl.duckdb` table `games`. Teams enumerated per season from standings (handles relocations); de-duped by game id; upserts (re-run refreshes the live season). |
| `../notebooks/09_nhl_model.ipynb` | Elo+feature holdout (accuracy/log-loss/calibration), final Elo ratings, skill-over-time. |

## Honest result (2010–2026, ~18k games; 2012-13 lockout season skipped)

Modest but **real** skill: ~56% accuracy / log-loss 0.677 out-of-sample, vs
always-pick-home 0.543 / 0.690. Top Elo teams are legit recent contenders
(Colorado / Carolina / Tampa / Dallas). Like baseball, the ceiling is a single
dominant per-game factor team-Elo can't see: the **starting goalie** (the NHL analog
of the MLB starting pitcher). A goalie rating (save% / goals-saved-above-expected,
point-in-time) is the obvious next feature. Elo params: K=6, HFA=30 (home base rate
~0.54). Refresh via `python research/nhl/ingest_nhl.py`.
