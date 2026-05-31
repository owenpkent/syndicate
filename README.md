# Sportsball: Autonomous Sports Analytics

[![Build Status](https://img.shields.io/badge/Build-Passing-brightgreen)](https://github.com/owenpkent/sportsball)
[![Python Version](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Sportsball is an autonomous, distributed-agent quantitative trading pipeline and validation environment. Optimized for high-performance headless servers, this system orchestrates an ensemble of specialized micro-agents to ingest sports data, calculate real-time expected value ($EV$), detect cross-market arbitrage opportunities, and execute optimized capital allocations.

---

## ─── Quick Start ───

### 1. Requirements
*   **Hardware:** Optimized for 16+ thread CPU architectures (e.g., AMD Ryzen 9).
*   **Software:** Docker & Docker Compose, Python 3.12+ (for host-side tools).

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

---

## ─── Performance Visualization ───

Sportsball includes a suite of Python-driven visualization tools to verify alpha:

*   **[Walk-Forward Simulation](scripts/visualize_backtest.py)**: Replicates real-time model learning and trading over 500+ games. Run with `make backtest-viz`.
*   **[PnL Equity Curve](scripts/visualize_pnl.py)**: Visualizes bankroll growth and volatility. Run with `make plot`.
*   **[Model Calibration](scripts/visualize_calibration.py)**: Diagnostic tool for probability accuracy. Run with `make calibrate`.

---

## ─── Production Utilities ───

Professional-grade tools for system health and quantitative audit:

*   **[CLV Tracker](scripts/analyze_clv.py)**: Quantify your betting edge by tracking Closing Line Value. Run with `make clv`.
*   **[Model Evaluator](scripts/evaluate_stats.py)**: Audit probability accuracy using Brier Score and Log-Loss. Run with `make evaluate`.
*   **[Health Monitor](scripts/monitor_agents.py)**: Real-time "heartbeat" check for the entire cluster. Run with `make health`.
*   **[Advanced Stats Fetcher](scripts/fetch_nba_stats.py)**: Enrichment tool for real-time NBA features. Run with `make fetch-stats`.

---

## ─── Documentation Wiki ───

For deep dives into specific system components, refer to our documentation library:

*   **[Quantitative Handbook](docs/QUANT.md)**: Explore the mathematical engine, including $EV$ calculation, Kelly Criterion sizing, Logistic Regression, and Monte Carlo simulations.
*   **[System Architecture](docs/ARCHITECTURE.md)**: Detailed topology of the "Cluster in a Box" design, Redis stream integration, and micro-agent specifications.
*   **[Developer Guide](docs/DEVELOPMENT.md)**: Step-by-step instructions for running backtests, monitoring real-time performance via the Dashboard, and extending agent functionality.
*   **[Quantitative Resources](docs/RESOURCES.md)**: Industry literature, mathematical foundations (Moneyball, Dixon-Coles), and data provider specifications.

---

## ─── System Architecture Overview ───

The architecture executes a "Cluster in a Box" design pattern using Docker containers to isolate specialized agent roles. This ensures multi-threaded efficiency across CPU cores and zero dependency cross-contamination.

```text
              ┌───────────────── [ Oracle Agent ] (Sharp Market Odds)
              │
              ▼
[ Redis Stream / Message Broker ] ◄─► [ Analytics Engine ] ──► [ DB / Log Layer ]
▲                             │
│                             ▼
└───────────────── [ Scout Agent ] (DEX / Polymarket WebSockets)
│
▼
[ Sniper Agent ] (Executioner & Paper Trader)
```

---

## ─── Directory Structure ───

```text
.
├── [docs/](docs/)                     # Deep-dive documentation wiki
├── [config/](config/)                 # Global parameters & DB init scripts
├── data/                              # Persistent volumes (git-ignored)
├── [src/](src/)                       # Micro-agent source code
│   ├── [analytics_engine/](src/analytics_engine/)     # Mathematical modeling (SciPy/Sklearn)
│   ├── [oracle_agent/](src/oracle_agent/)             # Data scrapers & ingestion
│   ├── [scout_agent/](src/scout_agent/)               # WebSocket market watchers
│   ├── [sniper_agent/](src/sniper_agent/)             # Order execution & paper logs
│   └── [dashboard.py](src/dashboard.py)               # Real-time performance UI
└── [tests/](tests/)                   # Simulation & backtesting suite
```

---

## ─── Core Modules ───

*   **[Analytics Engine](src/analytics_engine/main.py)**: The central processing loop coordinating model prediction and risk management.
*   **[Arbitrage Logic](src/analytics_engine/arbitrage_engine.py)**: Real-time cross-venue discrepancy detection.
*   **[Portfolio Manager](src/analytics_engine/portfolio_manager.py)**: Global exposure and correlation guards.
*   **[Model Trainer](src/analytics_engine/model_trainer.py)**: Automated Logistic Regression and Elo training pipeline.
*   **[Backtest Pipeline](tests/backtest_pipeline.py)**: Historical simulation and strategy validation engine.

---

## ─── License ───

Distributed under the MIT License. See `LICENSE` for more information.
