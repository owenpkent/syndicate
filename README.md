# Sportsball: Autonomous Sports Analytics

[![Build Status](https://img.shields.io/badge/Build-Passing-brightgreen)](https://github.com/owenpkent/sportsball)
[![Python Version](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Sportsball is an autonomous, distributed-agent quantitative trading pipeline and validation environment. It orchestrates an ensemble of specialized micro-agents to ingest sports data, calculate real-time expected value ($EV$), detect cross-market arbitrage opportunities, and size capital with the fractional Kelly criterion.

> **What this is (and isn't):** Sportsball runs in **paper-trading / simulation mode** by default (`EXECUTION_MODE=PAPER`). It is a research and demonstration harness for sports-market modeling and execution logic — it does **not** place real bets and ships **no proven edge** out of the box. The default probability source is randomized; real alpha requires training a model on backfilled history. See **[Known Limitations](docs/ARCHITECTURE.md#5-known-limitations)** for an honest accounting of what works end-to-end today.

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
*   **CLI Dashboard:** `make dashboard`
*   **Performance Charts:** `make plot` (PnL) or `make calibrate` (Model Accuracy)

### 4. Get Data & Train a Model
The Engine abstains until it has a trained model. Load **real NBA history for
free (no API key)** and train:
```bash
make ingest-nba   # thousands of real games from nba_api -> events
make retrain      # optimize Elo params + fit the win-probability model
```
The Retrainer agent then keeps the model fresh on a schedule, and the Engine
hot-reloads it. (For closing-line value, add `sportsball-backfill` with a
`RUNDOWN_API_KEY`.)

---

## ─── Performance Visualization ───

Sportsball includes a suite of Python-driven visualization tools to verify alpha:

*   **[Walk-Forward Simulation](scripts/visualize_backtest.py)**: Replicates real-time model learning and trading over 500+ games. Run with `make backtest-viz`.
*   **[PnL Equity Curve](scripts/visualize_pnl.py)**: Visualizes bankroll growth and volatility. Run with `make plot`.
*   **[Model Calibration](scripts/visualize_calibration.py)**: Diagnostic tool for probability accuracy. Run with `make calibrate`.

---

## ─── Production Utilities ───

Professional-grade tools for system health and quantitative audit:

*   **[CLV Tracker](src/sportsball/tools/clv.py)**: Quantify your betting edge by tracking Closing Line Value. Run with `make clv`.
*   **[Model Evaluator](src/sportsball/tools/evaluate.py)**: Audit probability accuracy using Brier Score and Log-Loss. Run with `make evaluate`.
*   **[Health Monitor](src/sportsball/tools/health.py)**: Real probe of Redis/Postgres, queue depth, and exposure (exits non-zero when degraded). Run with `make health`.
*   **[Advanced Stats Fetcher](scripts/fetch_nba_stats.py)**: Enrichment tool for real-time NBA features. Run with `make fetch-stats`.

---

## ─── Documentation Wiki ───

For deep dives into specific system components, refer to our documentation library:

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
│   ├── [quant/](src/sportsball/quant/)            # Pure math: odds, poisson, models, arbitrage, portfolio
│   ├── [markets/](src/sportsball/markets/)        # Polymarket Gamma discovery
│   ├── [agents/](src/sportsball/agents/)          # oracle · scout · engine · sniper · settlement · retrainer
│   ├── [pipelines/](src/sportsball/pipelines/)    # optimize · train · retrain · backfill · ingest_nba
│   └── [tools/](src/sportsball/tools/)            # dashboard · health · clv · evaluate
├── [scripts/](scripts/)               # Host visualizations & stats enrichment
└── [tests/](tests/)                   # Unit suite (71 tests) + backtest pipeline
```

---

## ─── Core Modules ───

*   **[Analytics Engine](src/sportsball/agents/engine.py)**: Consumes signals, models win probability, prices EV, sizes with Kelly, and gates on portfolio risk. Abstains when it has no trained model.
*   **[Arbitrage Logic](src/sportsball/quant/arbitrage.py)**: Real-time cross-venue discrepancy detection.
*   **[Portfolio Manager](src/sportsball/quant/portfolio.py)**: Global exposure and correlation guards.
*   **[Training Pipeline](src/sportsball/pipelines/train.py)**: Walk-forward Elo + Logistic Regression, writing the model the Engine loads.
*   **[Backtest Pipeline](tests/backtest_pipeline.py)**: Historical simulation and strategy validation engine.

---

## ─── Configuration ───

All runtime behavior is driven by two files. Copy `.env.example` to `.env` and edit as needed.

### Environment variables (`.env`)

| Variable | Default | Used by | Description |
|----------|---------|---------|-------------|
| `RUNDOWN_API_KEY` | _(unset)_ | Oracle, scraper | The Rundown API key. If unset/placeholder, the Oracle runs in **mock mode**. |
| `EXECUTION_MODE` | `PAPER` | Sniper | `PAPER` simulates fills with slippage. Any other value skips execution (live trading is not implemented). |
| `SLIPPAGE_TOLERANCE_PCT` | `0.005` | Sniper | Reject a simulated fill if slippage exceeds this fraction. |
| `POLLING_INTERVAL` | `30` | Oracle | Seconds between Oracle line pulls. |
| `SETTLEMENT_INTERVAL` | `60` | Settlement | Seconds between settlement sweeps. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `sportsball_admin` / `changeme_in_env` / `market_history` | Postgres + all DB clients | Database credentials. **Change the password** before any non-local use — every agent now reads these from the environment. |
| `REDIS_HOST` / `DB_HOST` | `redis` / `postgres` | all agents | Service hostnames (set automatically inside Compose; host-side tools default to `localhost`). |

### Strategy parameters (`config/settings.json`)

| Key | Default | Meaning |
|-----|---------|---------|
| `safety_buffer_ev` | `0.02` | Minimum EV required to emit an execution signal (model-variance cushion). |
| `kelly_multiplier` | `0.25` | Fraction of full Kelly to stake (quarter-Kelly). |
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
