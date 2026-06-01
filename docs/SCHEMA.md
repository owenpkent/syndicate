# Data Model

Sportsball's persistence is a small, normalized PostgreSQL schema defined in
[config/init.sql](../config/init.sql) and accessed exclusively through the
repository layer ([src/sportsball/store.py](../src/sportsball/store.py)) — no
agent or tool embeds raw SQL.

> **History:** the original schema coupled rows by string-matching
> `market_id LIKE '%' || event_id || '%'`. v0.2 replaced that with a real
> foreign key on `event_id`. See [ARCHITECTURE §5](ARCHITECTURE.md#5-known-limitations).

---

## Entity relationships

```text
                    ┌──────────────────────────┐
                    │          events          │   one row per game
                    │  event_id (PK)           │
                    │  home_team, away_team    │
                    │  status: SCHEDULED|FINAL │
                    │  home_score, away_score  │
                    │  home_close, away_close  │
                    └────────────┬─────────────┘
                                 │ event_id (FK)
                ┌────────────────┴───────────────┐
                ▼                                 ▼
      ┌───────────────────┐            ┌────────────────────┐
      │      signals      │            │       trades       │
      │  every modeled    │            │  executed paper    │
      │  evaluation       │            │  positions + PnL   │
      │  side: HOME|AWAY  │            │  side, status, pnl │
      └───────────────────┘            └────────────────────┘

   team_advanced_stats (team_name PK)  — efficiency metrics, joined by name for enrichment
```

---

## Tables

### `events` — the single source of truth for a game
A game starts life as a **`SCHEDULED`** stub (the Engine upserts it the first
time it sees a market, so signals/trades have something to reference), and is
later completed to **`FINAL`** with scores and closing lines by the backfill /
results feed.

| Column | Type | Notes |
|--------|------|-------|
| `event_id` | TEXT PK | **Canonical** id from `matching.canonical_event_id(sport, date, teams)`, e.g. `nba_20240115_lakers_at_celtics`. Venue-independent so the Oracle, backfill, NBA ingester, and Scout collapse the same game onto one row. |
| `sport_id` | INT | NBA=4, NFL=2, MLB=1, NHL=6 |
| `event_date` | TIMESTAMPTZ | |
| `home_team`, `away_team` | TEXT | |
| `status` | TEXT | `SCHEDULED` or `FINAL` |
| `home_score`, `away_score` | INT | Null until FINAL |
| `home_close`, `away_close` | NUMERIC | Closing decimal odds (basis for CLV) |

### `signals` — every modeled evaluation
One row per probability the Engine computed, whether or not it traded. Powers
calibration and model-evaluation tooling.

| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `event_id` | TEXT FK → events | |
| `side` | TEXT | `HOME` / `AWAY` — which side the prob is for |
| `source` | TEXT | Producer (RUNDOWN, MOCK, …) |
| `odds`, `true_prob`, `ev` | NUMERIC | The priced market |

### `trades` — executed paper positions
| Column | Type | Notes |
|--------|------|-------|
| `id` | BIGSERIAL PK | |
| `event_id` | TEXT FK → events | |
| `side` | TEXT | `HOME` / `AWAY` |
| `market_id` | TEXT | Venue identifier; the exposure-hash key. **Not** used for joins. |
| `executed_odds`, `stake_frac` | NUMERIC | |
| `status` | TEXT | `OPEN` → `WIN`/`LOSS`; or `FAILED`, `ARB_LEG` |
| `is_arb` | BOOL | Part of an arbitrage set |
| `pnl` | NUMERIC | Realized, in stake-fraction units, set at settlement |
| `settled_ts` | TIMESTAMPTZ | |

### `team_advanced_stats` — enrichment features
Off/Def/Net rating + pace + TS%, keyed by `team_name`, refreshed by
`make fetch-stats`. Joined by name (fuzzy `ILIKE`) during Engine enrichment. The
`player_strength NUMERIC` column (nullable) holds a DuckDB-derived roster-strength
scalar written by `make player-strength` ([compute_player_strength.py](../scripts/compute_player_strength.py));
it feeds the model's `player_strength_diff` feature and is NULL/0 when not computed.

> **Migration:** `config/init.sql` only runs on an empty volume. Existing
> deployments add the column with
> `ALTER TABLE team_advanced_stats ADD COLUMN IF NOT EXISTS player_strength NUMERIC(10,4);`

### `team_strength_pit` — point-in-time roster strength
One row per **team-game**, written by `make roster-pit`
([compute](../scripts/precompute_roster_pit.py)) from the DuckDB player logs:
`(team_name, game_date, season, roster_strength)`, where `roster_strength` uses only
that team's *prior* games in the season (leakage-free). Joined by the trainer to feed
the model's `player_strength_diff` feature; serving uses the snapshot's latest value.

### `team_availability_pit` — point-in-time roster availability
One row per **team-game**, written by `make ingest-injuries`
([compute](../src/sportsball/pipelines/ingest_injuries.py)) from the DuckDB player
logs: `(team_name, game_date, season, availability)` — the season-to-date strength of
the players actually available (who logged minutes), scored from prior games only.
Feeds the model's `availability_diff` feature (v3); the Engine's serve path reads the
latest value per team. Empty → the feature is inert (neutral 0).

> Closing odds (`events.home_close`/`away_close`) are populated by `make ingest-odds`
> ([ingest_odds](../src/sportsball/pipelines/ingest_odds.py)); de-vigged they feed the
> v4 `market_logit` feature and unlock `make clv`.

### Model artifacts (`models/`, not a DB table)
`make train` writes three files the Engine's `ModelBundle` loads together:
`win_prob_model.pkl` (the scaler+logistic Pipeline, by default **ensembled** with a
gradient-boosted tree), `team_state.json` (per-team snapshot — Elo, last-game-date,
form, **point-in-time `net_eff`/`roster`/`season`/`availability`** — for symmetric
serving with a new-season reset), and `model_meta.json` (the feature contract + hfa +
the auto-selected `calibration` spec + schema version). A schema/width mismatch makes
the Engine abstain until `make retrain`. See [QUANT.md](QUANT.md#2-the-win-probability-model-live).

---

## Lifecycle of one bet

1. **Engine** sees a market → `upsert_event_stub(event_id, home, away)` (SCHEDULED)
   → `record_signal(event_id, side, …)`.
2. If EV clears the buffer, it emits an execution signal carrying `event_id`,
   `side`, and teams (no re-parsing downstream).
3. **Sniper** fills it (paper) → `record_trade(… status='OPEN', market_id=…)` and
   sets exposure in Redis `active_trades[market_id]`.
4. **Backfill / results feed** completes the event → `upsert_event_result(… 'FINAL', scores, closes)`.
5. **Settlement** joins `trades` ⋈ `events` on `event_id` where the event is
   FINAL, grades by `side`, writes `status` + `pnl` + `settled_ts`, and clears
   `active_trades[market_id]` (the reaper).

Because every link is an `event_id` foreign key, settlement, CLV, and evaluation
are exact joins — not substring matches.

---

## DuckDB analytics store

Separately from the operational Postgres schema, two host-side scripts land a
large, free NBA history into a single portable **DuckDB** file
(`data/sportsball.duckdb`) for offline / Moneyball-style analysis. This avoids
the running-container schema and needs no server — just the embedded file.

> **Status:** this store is **parallel** to the model pipeline — `train` /
> `retrain` still read from Postgres. It is a research dataset, not (yet) wired
> into the Engine. Bridging it (a DuckDB-backed `Store`, or a load into Postgres
> `events`) is a future step. See [ARCHITECTURE §5](ARCHITECTURE.md#5-known-limitations).

Both ingests are idempotent (re-running upserts) and write incrementally, so a
rate-limited run resumes cleanly. They reuse `matching.canonical_event_id`, so
DuckDB rows share the **same `event_id`** as the Postgres `events` table.

### `events` — team-level game results
Written by [`scripts/ingest_nba_duckdb.py`](../scripts/ingest_nba_duckdb.py)
(`leaguegamelog` at team level). One row per game; ~49K games across 40+ seasons.

| Column | Type | Notes |
|--------|------|-------|
| `event_id` | TEXT PK | Canonical id, matches Postgres `events` |
| `sport_id` | INT | NBA = 4 |
| `season` | TEXT | e.g. `2024-25` |
| `event_date` | TIMESTAMP | |
| `home_team`, `away_team` | TEXT | |
| `status` | TEXT | `FINAL` |
| `home_score`, `away_score` | INT | |
| `home_close`, `away_close` | DOUBLE | NULL (no odds source; scores train the model) |

### `player_game_logs` — individual player box scores ("Moneyball")
Written by [`scripts/ingest_player_stats_duckdb.py`](../scripts/ingest_player_stats_duckdb.py)
(`leaguegamelog` at player level). **1,012,331 player-games** across 3,584 players
(99.98% linked to an `event_id`),
keyed `(player_id, game_id)` and tagged with the game's `event_id` so it joins
straight onto `events`.

| Column | Type | Notes |
|--------|------|-------|
| `player_id`, `game_id` | BIGINT / TEXT | Composite PK |
| `event_id` | TEXT | FK-style link to `events` (NULL only for a few unparseable matchups) |
| `season`, `game_date` | TEXT / TIMESTAMP | |
| `player_name`, `team_id`, `team_abbreviation`, `team_name` | | |
| `is_home` | BOOLEAN | Derived from the MATCHUP string |
| `wl` | TEXT | `W` / `L` |
| box-score stats | DOUBLE | `min, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta, ft_pct, oreb, dreb, reb, ast, stl, blk, tov, pf, pts, plus_minus, fantasy_pts` (older seasons NULL where unavailable) |

```bash
python scripts/ingest_nba_duckdb.py            # team results, 1983-84 .. current
python scripts/ingest_player_stats_duckdb.py   # player box scores, same range
# narrower: --seasons 2024-25,2025-26  |  --start 1996-97
```

### `team_advanced_game_logs` — possession-based team stats
Written by [`scripts/ingest_team_advanced_duckdb.py`](../scripts/ingest_team_advanced_duckdb.py)
(`make ingest-team-advanced`; `TeamGameLogs` measure_type=Advanced, free/no key).
**71,092 team-games (1996-2026, 99.97% linked)**, keyed `(team_id, game_id)`:
`off_rating, def_rating, net_rating, pace, ts_pct, efg_pct, ast_pct, ast_to,
oreb_pct, dreb_pct, reb_pct, tm_tov_pct, pie`. Raw material for a totals/pace
model. (A possession net rating was measured vs the margin-based `net_rating_diff`
and found redundant with Elo — see `scripts/possession_experiment.py` — so it is
**not** a win-prob feature; the data is kept for totals.)

### `odds_quotes` — per-book h2h + totals (line-shopping / totals)
Written by the one-time paid historical pull
([`scripts/backfill_odds_markets_duckdb.py`](../scripts/backfill_odds_markets_duckdb.py),
248k quotes, 4,887 games 2022-2026, 23 books) **and** the free ongoing capture
([`scripts/capture_odds_quotes.py`](../scripts/capture_odds_quotes.py), `make
capture-quotes PHASE=open|close`). Keyed `(event_id, market, bookmaker, side,
phase)`: `market` (`h2h`/`totals`), `bookmaker`, `side` (team / `Over` / `Under` /
`Draw`), `point` (totals line; NULL for h2h), `price` (decimal), **`phase`**
(`open` = first sighting / `close` = latest near-tip), `captured_at`, **`sport`**
(`nba`/`mlb`/`wnba`/`wc`/… — steam is sport-agnostic, so the live capture runs on
whatever is in season; only `nba` joins our events table). Unlike `events.home_close`
(the consensus *median*), this keeps **every book's** quote. Uses: line-shopping
(best-available vs close), a totals model, and — via the `open`/`close` pair — the
**line-movement edge hunt** (find openers that systematically misprice → CLV).
The daily cron captures `open` (morning) + `close` (near tip) for free.

```bash
make ingest-team-advanced                      # possession stats, 1996-97 .. current
# odds_quotes is a one-time paid backfill; see OPERATIONS.md §9
```

### `odds_snapshots` — intraday per-book time series (book lead-lag)
Written by [`scripts/capture_snapshot.py`](../scripts/capture_snapshot.py)
(`make capture-snapshot SPORT=baseball_mlb`), an **append-only** capture run on a
dense intraday cron. Unlike `odds_quotes` (which keeps only open/close per game),
this keeps **every snapshot**, keyed `(event_id, market, bookmaker, side,
captured_at)` with `sport`, `point`, `price`, `commence_time`. Reconstructs each
book's price-vs-time trajectory → **book lead-lag** (which book moves first; beat
the laggard to the number = CLV by certainty). Live MLB capture (every 2h, free
tier); the deployable edge from ROADMAP "Modeling the market" §2.
