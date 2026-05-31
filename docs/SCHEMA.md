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
`make fetch-stats`. Joined by name (fuzzy `ILIKE`) during Engine enrichment.

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
