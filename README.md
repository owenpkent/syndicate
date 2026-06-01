# Sportsball: Autonomous Sports Analytics

[![CI](https://github.com/owenpkent/sportsball/actions/workflows/ci.yml/badge.svg)](https://github.com/owenpkent/sportsball/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Sportsball is an autonomous, distributed-agent quantitative trading pipeline and validation environment. It orchestrates an ensemble of specialized micro-agents to ingest sports data, calculate real-time expected value ($EV$), detect cross-market arbitrage opportunities, and size capital with the fractional Kelly criterion.

> **What this is (and isn't):** Sportsball runs in **paper-trading / simulation mode** by default (`EXECUTION_MODE=PAPER`). It is a research and demonstration harness for sports-market modeling and execution logic — it does **not** place real bets and ships **no proven edge** out of the box. The Engine **abstains entirely when it has no trained model** — it never stakes on a producer-supplied or random probability. Real alpha requires training on backfilled history and is gated on **Closing Line Value** vs. a sharp closing line, which needs a real odds feed. See **[Known Limitations](docs/ARCHITECTURE.md#5-known-limitations)** and the **[Roadmap](docs/ROADMAP.md)** for an honest accounting of what works end-to-end today.

> **Research findings (honest):** With real odds loaded (2011–2026) and a rigorous edge hunt, **no model beats the closing line** (sides CLV −1.67%, totals residual R² ≈ 0) — the market prices in everything our box-score features know. Line-shopping ≈ breakeven; the soft-book edge was real (2022–24) but **decayed**. The **one real, durable edge is line *movement* (steam)**: betting the side a total moves toward, at the *opening* number, wins 53–60%+ across **~56k games / 4 sports (NBA/MLB/NHL/NFL)** — a market-structure law, not basketball. The strategic pivot is **modeling the *market* (predicting the close/movement), not the *game*** — which shows a small but *positive* signal where game-modeling was zero. The live capture now records per-book open/close lines across in-season sports to chase it. Full writeup: **[Roadmap → Edge research](docs/ROADMAP.md)**.

---

## ─── Quick Start ───

### 1. Requirements
*   **Hardware:** Runs comfortably on any modern multi-core machine; the containerized agents are lightweight and I/O-bound.
*   **Software:** Docker & Docker Compose (runtime), Python 3.11+ (for host-side tools and visualizations).

### 2. Deployment
```bash
git clone https://github.com/owenpkent/sportsball.git
cd sportsball
cp .env.example .env
make setup            # Initialize local venv & dependencies
docker compose up -d --build
```

### 3. Verify & Monitor
*   **Live Logs:** `docker compose logs -f`
*   **Web Dashboard:** `make webui` → `http://127.0.0.1:8000` (KPIs, equity curve, CLV + model-status panels; runs offline on demo data with no DB)
*   **CLI Dashboard:** `make dashboard`
*   **Performance Charts:** `make plot` (PnL) or `make calibrate` (Model Accuracy)

### 4. Get Data & Train a Model
The Engine abstains until it has a trained model. Load **real NBA history for
free (no API key)** and train:
```bash
make bootstrap    # ensure schema (idempotent) + load DuckDB history -> events
make ingest-nba   # (or this) thousands of real games from nba_api -> events
make retrain      # optimize Elo params + fit the v4 win-probability model
make eval-duckdb  # out-of-sample walk-forward holdout (Brier/log-loss, no Postgres)
make measure-algos # quantify feature/ensemble/calibration lift on synthetic data
```
The Retrainer agent then keeps the model fresh on a schedule, and the Engine
hot-reloads it. For richer features, run `make player-strength` / `make roster-pit`
(roster), `make ingest-injuries` (point-in-time availability), and `make ingest-odds`
(real closing odds → `events.home_close/away_close`, which lights up `make clv`)
before `make retrain`.

Validate the live data sources any time with `make smoke` (checks the Gamma API,
nba_api, and the Polymarket CLOB WebSocket).

---

## ─── Performance Visualization ───

Sportsball includes a suite of Python-driven visualization tools to verify alpha:

*   **[Walk-Forward Simulation](scripts/visualize_backtest.py)**: Replicates real-time model learning and trading over 500+ games. Run with `make backtest-viz`.
*   **[PnL Equity Curve](scripts/visualize_pnl.py)**: Visualizes bankroll growth and volatility. Run with `make plot`.
*   **[Model Calibration](scripts/visualize_calibration.py)**: Diagnostic tool for probability accuracy. Run with `make calibrate`.

---

## ─── Production Utilities ───

Professional-grade tools for system health and quantitative audit:

*   **[CLV Tracker](src/sportsball/tools/clv.py)**: Closing Line Value — the **primary edge metric** — over all evaluated signals (largest sample) and filled trades. Run with `make clv`.
*   **[Model Evaluator](src/sportsball/tools/evaluate.py)**: Leads with the CLV edge gate, then Brier Score / Log-Loss as a calibration check. Run with `make evaluate`.
*   **[Health Monitor](src/sportsball/tools/health.py)**: Real probe of Redis/Postgres, queue depth, and exposure (exits non-zero when degraded). Run with `make health`.
*   **[Advanced Stats Fetcher](scripts/fetch_nba_stats.py)**: Enrichment tool for real-time NBA features. Run with `make fetch-stats`.

---

## ─── Slack Integration (optional) ───

Wire Sportsball into Slack for real-time alerts, a scheduled digest, and a
human-in-the-loop approval gate. **Everything is off by default** — with no
`SLACK_*` env vars the pipeline runs exactly as before.

*   **Alerts**: the Sniper posts paper fills, Settlement posts WIN/LOSS + PnL,
    and `make health` posts on degradation. Needs a bot token **or** an incoming
    `SLACK_WEBHOOK_URL`.
*   **[Daily digest](src/sportsball/tools/digest.py)**: trailing-24h PnL, open
    exposure, trade/signal counts, and model freshness. Run with `make digest`
    (or cron `docker compose run --rm digest`).
*   **[Approval gate](src/sportsball/agents/approver.py)** (`sportsball-approver`):
    set `SLACK_REQUIRE_APPROVAL=true` and high-EV signals are *suggested* in
    Slack with Approve/Reject buttons — only Approve forwards the trade to the
    Sniper. Uses **Socket Mode** (bot token + app-level token), so no public
    endpoint is exposed. Unactioned suggestions auto-expire after
    `SLACK_APPROVAL_TTL_SECS`.

Configure tokens in `.env` (see `.env.example`). Scopes: bot `chat:write`;
app-level token `connections:write` with Socket Mode enabled.

---

## ─── Documentation Wiki ───

For deep dives into specific system components, refer to our documentation library:

*   **[White Paper](docs/WHITEPAPER.md)**: The system end to end — architecture, methodology, and the honest out-of-sample results (what works, what doesn't, and the measured limits).
*   **[Roadmap](docs/ROADMAP.md)**: What the system needs — to *measure* a real edge, to *have* one, and to *run* live — prioritized from the measured results.
*   **[Quantitative Handbook](docs/QUANT.md)**: Explore the mathematical engine, including $EV$ calculation, Kelly Criterion sizing, Logistic Regression, and Monte Carlo simulations.
*   **[System Architecture](docs/ARCHITECTURE.md)**: Detailed topology of the "Cluster in a Box" design, the Redis-backed signal pipeline, message schemas, and micro-agent specifications.
*   **[Data Model](docs/SCHEMA.md)**: The normalized `events`/`signals`/`trades` schema, entity relationships, and the lifecycle of a bet from signal to settled PnL.
*   **[Operations Runbook](docs/OPERATIONS.md)**: First run, the modeling loop, monitoring, resetting the database, and troubleshooting.
*   **[Developer Guide](docs/DEVELOPMENT.md)**: Step-by-step instructions for running backtests, monitoring real-time performance via the Dashboard, and extending agent functionality.
*   **[Quantitative Resources](docs/RESOURCES.md)**: Industry literature, mathematical foundations (Moneyball, Dixon-Coles), and data provider specifications.

---

## ─── System Architecture Overview ───

The architecture executes a "Cluster in a Box" design pattern using Docker containers to isolate specialized agent roles. This ensures multi-threaded efficiency across CPU cores and zero dependency cross-contamination.

```text
  [ Oracle Agent ]        [ Scout Agent ]
  (sharp book odds)       (Polymarket WS)
        │                       │
        └────────┬──────────────┘
                 ▼   RPUSH "market_signals"
        ┌─────────────────────┐
        │     Redis broker     │◄──── HSET "active_trades" (exposure)
        └─────────────────────┘
                 │   BRPOPLPUSH (reliable)
                 ▼
        ┌─────────────────────┐   events (stub), signals
        │  Analytics Engine    │ ───────────────►  [ PostgreSQL ]
        │  model · EV · Kelly  │   (abstains with no model)
        └─────────────────────┘
                 │   RPUSH "execution_signals" (event_id, side)
                 ▼
        ┌─────────────────────┐   trades (status=OPEN)
        │   Sniper Agent       │ ───────────────►  [ PostgreSQL ]
        │ (paper execution)    │
        └─────────────────────┘
                 ▲   set status WIN/LOSS + pnl, reap exposure
        ┌─────────────────────┐
        │  Settlement Agent    │ ◄── trades ⋈ events ON event_id (FK)
        └─────────────────────┘
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full signal lifecycle, schemas, and queue semantics.

---

## ─── Directory Structure ───

Everything is one installable Python package (`sportsball`) with a single Docker
image; each agent is a console entrypoint (`sportsball-oracle`, `-engine`, …).

```text
.
├── [docs/](docs/)                     # Deep-dive documentation wiki
├── [config/](config/)                 # settings.json (strategy params) & init.sql (schema)
├── models/                            # Trained model + Elo ratings (loaded by the Engine)
├── data/                              # Persistent volumes (git-ignored)
├── Dockerfile                         # Single base image for all roles
├── [src/sportsball/](src/sportsball/)             # The package
│   ├── config.py · db.py · broker.py · store.py · matching.py · logging_conf.py  # Infra + repository
│   ├── [quant/](src/sportsball/quant/)            # Pure math: odds, poisson, models, arbitrage, portfolio, features, calibration
│   ├── [markets/](src/sportsball/markets/)        # Polymarket Gamma discovery
│   ├── [agents/](src/sportsball/agents/)          # oracle · scout · engine · sniper · settlement · retrainer · approver
│   ├── [notify/](src/sportsball/notify/)          # Slack: blocks (pure) · slack (Notifier) · gate
│   ├── [web/](src/sportsball/web/)                # FastAPI dashboard: providers (demo/duckdb/postgres) · app
│   ├── [pipelines/](src/sportsball/pipelines/)    # optimize · train · retrain · bootstrap · backfill(_signals) · ingest_nba/injuries/odds
│   └── [tools/](src/sportsball/tools/)            # dashboard · webui · health · clv · evaluate · smoke · digest
├── [scripts/](scripts/)               # Host visualizations, stats enrichment, DuckDB ingest, offline dry-run & measurement
└── [tests/](tests/)                   # Unit suite (233 tests) + backtest pipeline
```

---

## ─── Core Modules ───

*   **[Analytics Engine](src/sportsball/agents/engine.py)**: Consumes signals, models win probability, **line-shops the best price across venues**, prices EV, sizes with **uncertainty-aware** fractional Kelly, and gates on portfolio risk. Abstains when it has no trained model.
*   **[Arbitrage Logic](src/sportsball/quant/arbitrage.py)**: Cross-venue discrepancy detection on an **order-independent matchup key** (aligns regardless of home/away).
*   **[Portfolio Manager](src/sportsball/quant/portfolio.py)**: Global exposure and correlation guards.
*   **[Training Pipeline](src/sportsball/pipelines/train.py)**: Walk-forward Elo + a 9-feature standardizing logistic **ensembled with a gradient-boosted tree**, with auto-selected (temperature/isotonic) **calibration**; writes the model the Engine loads.
*   **[Web Dashboard](src/sportsball/web/app.py)**: FastAPI KPIs, equity curve, CLV + model-status panels (`make webui`; demo/DuckDB/Postgres data sources).
*   **[Backtest Pipeline](tests/backtest_pipeline.py)**: Historical simulation and strategy validation engine.

---

## ─── Configuration ───

All runtime behavior is driven by two files. Copy `.env.example` to `.env` and edit as needed.

### Environment variables (`.env`)

| Variable | Default | Used by | Description |
|----------|---------|---------|-------------|
| `RUNDOWN_API_KEY` | _(unset)_ | Oracle, scraper | The Rundown API key. If unset/placeholder, the Oracle runs in **mock mode**. |
| `ODDS_API_KEY` | _(unset)_ | `ingest-odds` | The Odds API key for real closing odds → `events.home_close/away_close` (or use `make ingest-odds FILE=feed.json` offline). Unlocks CLV. |
| `EXECUTION_MODE` | `PAPER` | Sniper | `PAPER` simulates fills with slippage. Any other value skips execution (live trading is not implemented). |
| `SLIPPAGE_TOLERANCE_PCT` | `0.005` | Sniper | Reject a simulated fill if slippage exceeds this fraction. |
| `POLLING_INTERVAL` | `30` | Oracle | Seconds between Oracle line pulls. |
| `SETTLEMENT_INTERVAL` | `60` | Settlement | Seconds between settlement sweeps. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `sportsball_admin` / `changeme_in_env` / `market_history` | Postgres + all DB clients | Database credentials. **Change the password** before any non-local use — every agent now reads these from the environment. |
| `REDIS_HOST` / `DB_HOST` | `redis` / `postgres` | all agents | Service hostnames (set automatically inside Compose; host-side tools default to `localhost`). |
| `SLACK_BOT_TOKEN` / `SLACK_WEBHOOK_URL` | _(unset)_ | notifier, approver | Enable Slack alerts (bot token *or* webhook). All Slack features are off when unset. |
| `SLACK_APP_TOKEN` | _(unset)_ | approver | App-level token for Socket Mode — required for the interactive approval gate. |
| `SLACK_CHANNEL` | `#sportsball` | notifier | Channel for alerts/digest/suggestions. |
| `SLACK_REQUIRE_APPROVAL` | `false` | engine, approver | When `true` (with Socket Mode), high-EV signals need Slack Approve before trading. |
| `SLACK_APPROVAL_EV_THRESHOLD` / `SLACK_APPROVAL_TTL_SECS` | `0.10` / `900` | engine, approver | Min EV to gate; seconds before an unactioned suggestion auto-rejects. |

### Strategy parameters (`config/settings.json`)

| Key | Default | Meaning |
|-----|---------|---------|
| `safety_buffer_ev` | `0.02` | Minimum EV required to emit an execution signal (model-variance cushion). |
| `kelly_multiplier` | `0.25` | Fraction of full Kelly to stake (quarter-Kelly). |
| `uncertainty_scaling` | `true` | Shrink the Kelly stake by the model's calibration-confidence (less-certain model → smaller stake). |
| `model_ensemble` | `true` | Serve a 50/50 logistic + gradient-boosted-tree ensemble (vs. logistic alone). |
| `default_slippage` | `0.005` | Reference slippage for simulations. |
| `max_global_exposure_pct` | `0.15` | Hard cap on total simultaneous staked fraction across all open trades. |
| `correlation_penalty_multiplier` | `0.5` | Size multiplier applied when a new bet shares an event with an existing position. |

---

## ─── Testing ───

Unit tests cover the math core and arbitrage detection (no database or network required):

```bash
make test          # runs the host-side unit suite (pytest)
make backtest      # containerized strategy backtest over tests/mock_ticks.json
```

---

## ─── License ───

Distributed under the MIT License. See `LICENSE` for more information.
