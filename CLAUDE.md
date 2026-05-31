# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Sportsball is a **paper-trading / simulation** pipeline for sports-market
analytics — a distributed set of micro-agents that ingest odds, model win
probability, price expected value, size with fractional Kelly, and settle
results. It does **not** place real bets (`EXECUTION_MODE=PAPER`) and ships no
proven edge: probabilities come only from a trained model, and the Engine
**abstains** when there isn't one. Treat marketing language as aspirational;
keep docs honest.

Everything is one installable Python package (`sportsball`) running on a single
Docker image; each agent is a console entrypoint.

## Commands

```bash
make setup     # venv + `pip install -e ".[tools]"`
make test      # pytest — 71 unit tests, NO DB/Redis needed (uses in-memory fakes)
make backtest  # run tests/backtest_pipeline.py over mock ticks
make demo      # seed the DB with fake events/signals/trades for the tools
make dashboard / health / clv / evaluate / plot / calibrate / backtest-viz
make ingest-nba               # FREE real NBA history (nba_api, no key) -> events
make retrain                  # the modeling loop: optimize + train (Engine hot-reloads)

docker compose up -d --build  # full cluster (redis, postgres, 6 agents, dashboard)
docker compose down -v        # REQUIRED after a schema change (init.sql only runs on empty volume)
```

Run a single test: `./venv/bin/python3 -m pytest tests/test_quant_odds.py -q`

## Architecture (see docs/ for depth)

- **docs/ARCHITECTURE.md** — topology, signal lifecycle, queue semantics, limitations
- **docs/SCHEMA.md** — the `events`/`signals`/`trades` data model + bet lifecycle
- **docs/OPERATIONS.md** — runbook: first run, modeling loop, DB reset, troubleshooting

Flow: Oracle/Scout `RPUSH market_signals` → **Engine** models P_true, prices EV,
sizes/risk-checks, `RPUSH execution_signals` → **Sniper** paper-fills, sets
exposure → **Settlement** grades vs FINAL events, writes PnL, reaps exposure.

Package layout (`src/sportsball/`):
- `config.py` `db.py` `broker.py` `store.py` `matching.py` `logging_conf.py` — infra + repository
- `quant/` — pure math (odds, poisson, models, arbitrage, portfolio); **no I/O imports**
- `markets/` — Polymarket Gamma discovery (pure `parse_markets` + networked `fetch_markets`)
- `agents/` — oracle, scout, engine, sniper, settlement, retrainer (each has `main()`)
- `pipelines/` — optimize, train, retrain, backfill, ingest_nba (run on demand)
- `tools/` — dashboard, health, clv, evaluate

Events are keyed by a **canonical `event_id`** (`matching.canonical_event_id`,
e.g. `nba_20240115_lakers_at_celtics`) so the Oracle, backfill, NBA ingester, and
Scout collapse the same game onto one row. It contains no `-` (safe in `market_id`).

## Conventions (follow these)

- **All SQL lives in `store.py`** (the repository layer). Agents/tools call typed
  methods; do not embed SQL elsewhere.
- **Joins are FK joins on `event_id`.** Never reintroduce `LIKE '%'||id||'%'` or
  ad-hoc `market_id.split("-")` outside `store.parse_market_id`.
- **`market_id` format is `SOURCE-EVENTID-PARTICIPANT`** — the contract between
  producers and the Engine. Parse it once (Engine), then pass `event_id`/`side`.
- **The Engine only trades on a modeled probability.** Keep `require_model` true;
  never make it stake on a producer-supplied/random `true_prob`.
- **`quant/` stays pure** (no DB/redis/network) so the math is unit-testable.
- **Logging via `logging_conf.get_logger(name)`**, not `print` or bare
  `basicConfig`. **DB credentials via env** (`config.DBConfig`), never hardcoded.
- New agent/pipeline/tool → add a `main()` and register it in `pyproject.toml`
  `[project.scripts]`.

## Testing

- Unit tests use in-memory fakes (`tests/fakes.py`: `FakeRedis`, `FakeBroker`,
  `FakeDB`, `FakeBundle`) — no infrastructure required. Agent logic is tested by
  injecting these into the functions' explicit dependencies.
- `tests/conftest.py` puts `src/` on the path, so tests run with or without an
  editable install. Add a test for any `quant/` or `store` change and keep
  `make test` green.

## Gotchas

- **Schema reset:** Postgres runs `config/init.sql` only on an empty data
  directory. After changing the schema, `docker compose down -v` (wipes the
  volume) then `up`.
- **Root-owned remnant:** `src/analytics_engine/` is leftover from the
  pre-refactor layout (root-owned, created by an old container). It's gitignored;
  remove with `sudo rm -rf src/analytics_engine` when convenient. Model artifacts
  now live in `./models/`.
- **Commit trailer:** end commit messages with
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Status

Phases 1–3 done: package refactor, normalized schema, live Polymarket discovery,
canonical event ids, free NBA data ingest, and an automated retrain loop. The
remaining caveats are **live validation** (Gamma/CLOB/nba_api parsing matches
documented shapes but isn't exercised in CI) and **cross-venue arb** (depends on
Polymarket sports markets exposing a parseable matchup). See docs/ARCHITECTURE.md §5.
