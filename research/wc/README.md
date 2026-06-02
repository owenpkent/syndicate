# research/wc — World Cup / international football model

Soccer is a **3-outcome** problem (Win/Draw/Loss), played on mostly neutral ground at
the World Cup, with no seasons — so it gets a purpose-fit model, not the binary
NBA/MLB pipeline.

| File | What |
|---|---|
| `ingest_intl.py` | Download the free martj42 "international results 1872–present" CSV → `data/wc.duckdb` table `matches` (~49k games incl. `neutral` flag and future 2026 fixtures). |
| `soccer_elo.py` | Continuous national-team **Elo** — neutral-aware home advantage, World-Football-Elo goal-difference multiplier, point-in-time (no leakage). Pure + self-checked. |
| `../notebooks/08_world_cup_model.ipynb` | 3-class (W/D/L) multinomial on the pre-match Elo edge (+ `|edge|` so draws peak when teams are even); overall vs **World-Cup-specific** holdout, top-team Elo, calibration, and **2026 fixture predictions**. |

## Honest result

Elo recovers the real hierarchy (Argentina / France / Brazil / Spain on top). Out-of-
sample on the **2018 + 2022 World Cups** (trained only on pre-2018 data, 128 matches):
**3-way accuracy ~50.8%** vs naive 41.4%, log-loss ~1.01. Lower than the binary
NBA/MLB numbers by design — soccer has a third outcome and the World Cup strips out the
easy qualifier mismatches (overall internationals score ~60% 3-way). Real skill on a
deliberately balanced, high-draw competition. Levers: recent form / squad strength and
ingested World Cup odds. Refresh via `python research/wc/ingest_intl.py`.
