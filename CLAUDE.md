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
make test      # pytest — 154 unit tests, NO DB/Redis/network needed (uses in-memory fakes)
make backtest  # run tests/backtest_pipeline.py over mock ticks
make demo      # seed the DB with fake events/signals/trades for the tools
make dashboard / health / clv / evaluate / plot / calibrate / backtest-viz
make webui                    # FastAPI web dashboard (auto: Postgres -> DuckDB -> demo)
make smoke     # validate LIVE integrations (Gamma API, nba_api, CLOB WebSocket)
make ingest-nba               # FREE real NBA history (nba_api, no key) -> events
make bootstrap                # idempotent schema apply + load DuckDB history -> Postgres events
make retrain                  # the modeling loop: optimize + train (Engine hot-reloads)
make backfill-signals         # persist model predictions as signals (recent window) for evaluate
make eval-duckdb              # out-of-sample walk-forward holdout vs DuckDB (no Postgres)
make roster-pit               # point-in-time roster strength -> team_strength_pit
make ingest-injuries          # point-in-time roster availability -> team_availability_pit
make ingest-odds              # real closing odds -> events.home/away_close (FILE= or ODDS_API_KEY; --duckdb for offline)
sportsball-sbro-to-feed       # convert SBRO export/mirror archive -> ingest-odds feed JSON
make backfill-odds-history    # Odds API HISTORICAL snapshots -> events (recent closing lines; ~10cr/game-day)
make capture-odds             # Odds API LIVE snapshot -> today's events (~1cr; free-tier, daily cron)
make capture-quotes PHASE=open|close  # per-book h2h+totals -> DuckDB odds_quotes (line-movement hunt)
make ingest-team-advanced     # nba_api possession stats (off/def/net rating, pace, PIE) -> DuckDB
make clv                      # Closing Line Value — the primary edge KPI (needs odds + signals)
make measure-features         # holdout feature ablation; model-quality = calibration + sweep
make backtest-sim             # walk-forward betting backtest (ROI/win%/drawdown vs synthetic market+vig)

docker compose up -d --build  # full cluster (redis, postgres, agents, dashboard, approver)
docker compose down -v        # REQUIRED after a schema change (init.sql only runs on empty volume)
make digest                   # post the trailing-24h Slack digest (no-op without SLACK_*)
make backup                   # snapshot Postgres (pg_dump) + DuckDB + models -> backups/<ts>/
make restore DIR=backups/<ts> # inverse of make backup (prompts; DESTRUCTIVE)
```

**Backups** (see docs/OPERATIONS.md §8): `make backup` dumps Postgres via an
in-container `pg_dump` (the data dir is uid-70/0700, so a host `cp -r` gets
*permission denied* — the logical dump is the supported path), copies the DuckDB
store + `models/`, and prunes to the newest `KEEP` (default 14). Set `MIRROR=` to
a mounted path (e.g. an `/etc/fstab` CIFS mount — **not** a `gvfs` desktop mount,
which cron can't see) for an off-site copy.

## Slack integration (optional)

All Slack features are **off by default** — with no `SLACK_*` env vars the
notifier is a no-op and the Engine pushes straight to `EXECUTION_SIGNALS`, so
behavior is identical to pre-Slack. Config lives in `SlackConfig` (config.py),
mirroring the `rundown_api_key` precedent. Alerts (Sniper fills, Settlement
PnL, degraded `health`) need a bot token **or** `SLACK_WEBHOOK_URL`. The
human-in-the-loop **approval gate** (`sportsball-approver`) needs a bot token +
app-level token (Socket Mode — no public endpoint) **and**
`SLACK_REQUIRE_APPROVAL=true`: high-EV signals are held in a Redis pending hash,
posted with Approve/Reject buttons, and only forwarded to the Sniper on Approve.
Invariants: the notifier **never raises into an agent hot path**; the Engine
never calls Slack (it only enqueues); double-clicks/expiry are idempotent via
`broker.pop_pending` (HDEL-wins); `EXECUTION_MODE` stays `PAPER`.

Run a single test: `./venv/bin/python3 -m pytest tests/test_quant_odds.py -q`

## Architecture (see docs/ for depth)

- **docs/WHITEPAPER.md** — system end-to-end: architecture, methodology, honest out-of-sample results
- **docs/ROADMAP.md** — what it needs to measure / have / run a real edge (prioritized)
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
- `agents/` — oracle, scout, engine, sniper, settlement, retrainer, approver (each has `main()`)
- `pipelines/` — optimize, train, retrain, backfill, ingest_nba (run on demand)
- `tools/` — dashboard, health, clv, evaluate, smoke (live-integration check), digest
- `notify/` — Slack: `blocks` (pure Block Kit), `slack` (`Notifier`, no-op when
  unconfigured + error-isolating), `gate` (`ApprovalGate` routing)
- `web/` — FastAPI dashboard: `providers` (demo / DuckDB / Postgres `DataProvider`s
  + on-disk `model_status`), `app` (`create_app` + the self-contained HTML page).
  Runs offline on demo data; `sportsball-webui` / `make webui`. The `[web]` extra
  (fastapi/uvicorn/httpx) is optional; web tests `importorskip` so the suite stays
  green without it.

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

Phases 1–4 done: package refactor, normalized schema, live Polymarket discovery,
canonical event ids, free NBA data ingest, an automated retrain loop, and the
v0.4 Slack integration (alerts, `digest`, and the optional Socket Mode approval
gate — all off by default). Live integrations are validated via `make smoke`
(confirmed: real Gamma markets, 1230 nba_api games/season, CLOB socket connects)
but not yet in automated CI. The Slack approval gate is unit-tested with injected
fakes; its live Socket Mode round-trip needs a real workspace and hasn't been
exercised. A separate DuckDB research store (team + player game logs, see
docs/SCHEMA.md) is parallel to — not yet wired into — the model pipeline. The
the Polymarket path now produces priceable, canonically-keyed signals for
head-to-head markets (`markets/polymarket.parse_game_market`); the remaining
open item is order-independent **cross-venue arb** alignment (Polymarket doesn't
expose home/away). CI runs the suite on push (`.github/workflows/ci.yml`).
`make bootstrap` rebuilds the schema non-destructively and loads DuckDB history,
so the full Postgres loop (retrain → backfill-signals → evaluate) runs locally.
See docs/ARCHITECTURE.md §5.

The win-probability model is **v4** (schema_version 4): MOV-adjusted Elo with
season carryover, a shared `quant/features.py` builder (9 features) used by both
train and serve, a `Pipeline(StandardScaler, LogisticRegression)`, and a
player-derived roster-strength feature from the DuckDB logs (`make player-strength`),
and post-hoc **temperature calibration** (fixes out-of-sample over-confidence; `T`
persisted in `model_meta.json`, applied in `ModelBundle`). Holdout ablation
(`make model-quality` / `measure-features`) shows Elo + rest/b2b/form carry the
lift; the enrichment features are now **point-in-time** (season-to-date): net-eff
is computed in the Elo walk (adds −0.0009, where the current-season version added
~0) and roster strength from `team_strength_pit` (collinear with net-eff → ~0,
kept at weight ~0). The v3 addition is **`availability_diff`** — point-in-time
roster availability (the injuries lever): `make ingest-injuries` derives a
leakage-free per-team-game availability score from the DuckDB player logs (who
actually played, scored from prior games only) into `team_availability_pit`; the
trainer joins it and the Engine's serve path reads the latest value per team.
With no availability rows the feature is **inert (neutral 0)** and the model
behaves exactly as v2 — same "plumbing ready, blocked on data" posture as the
odds feed; once availability data lands a retrain activates it. The v4 addition is
**`market_logit`** — the logit of the **no-vig market probability** the home side
wins (Benter's lever: the market line as a *model input*, not just the EV
benchmark). Training de-vigs `events.home_close/away_close` (`make ingest-odds`);
the Engine de-vigs the best two-sided price from the arbitrage book at serve.
Real closing odds are now **loaded, served, and measured end-to-end**, 2011-2026:
the SBRO mirror (2011-2022, `sportsball-sbro-to-feed`) plus **The Odds API
historical backfill** (2022-present, `make backfill-odds-history`, ET-localized)
give **17,338 priced games**, all behind the vig guard (`passes_vig_guard`). The
served v4 model is retrained on them, so `market_logit` is **active** (no longer
inert). With recent games lined, the holdout lift is un-diluted: **log-loss
0.6308 -> 0.6236, accuracy 0.6463 -> 0.6587** across 7,159 lined test games.
Ongoing odds stay current for free via `make capture-odds` (LIVE endpoint, ~1
credit/day, daily cron). **Honest edge gate — first real CLV** (`make clv`, v4
signals vs closing, 2025-26): **avg CLV -1.67%, beat-rate 53% -> SUB-PAR**: a
good predictor that does **not** beat the sharp close (yet). It still falls back
to neutral 0 where no line exists.
Post-hoc calibration is now
**auto-selected** (temperature *or* isotonic, whichever generalizes; persisted as
`model_meta.calibration`), and the Engine **shrinks the Kelly stake by the
model's calibration-confidence** (`strategy.uncertainty_scaling`). The served model
is by default a 50/50 **ensemble** of the standardizing logistic and a
gradient-boosted tree (`quant/models.EnsembleModel`, `strategy.model_ensemble`;
same 9-feature contract, so no schema change — it pickles transparently into the
bundle). All point-in-time features reset at season boundaries, symmetric
train/serve. Artifacts are
`models/{win_prob_model.pkl, team_state.json, model_meta.json}`; a schema/width
mismatch makes the Engine abstain until `make retrain`. After pulling these
changes the shipped model is stale — run `make retrain` to regenerate. See
docs/QUANT.md for the algorithm.
