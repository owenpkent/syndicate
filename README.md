# Autonomous Sports Analytics Syndicate (Project: Syndicate)

[![Build Status](https://img.shields.io/badge/Build-Passing-brightgreen)](https://github.com/owenpkent/syndicate)
[![Python Version](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An autonomous, distributed-agent quantitative trading pipeline and validation environment. Optimized for high-performance headless servers, this system orchestrates an ensemble of specialized micro-agents to ingest sports data, calculate real-time expected value ($EV$), detect cross-market arbitrage opportunities, and execute optimized capital allocations.

---

## ─── Quick Start ───

### 1. Requirements
*   **Hardware:** Optimized for 16+ thread CPU architectures (e.g., AMD Ryzen 9).
*   **Software:** Docker & Docker Compose installed on Ubuntu 24.04.

### 2. Deployment
```bash
git clone https://github.com/owenpkent/syndicate.git
cd syndicate
cp .env.example .env
docker compose up -d --build
```

### 3. Verify
Monitor the live autonomous loop across all agents:
```bash
docker compose logs -f
```

---

## ─── Documentation Wiki ───

For deep dives into specific system components, refer to our documentation library:

*   **[Quantitative Handbook](docs/QUANT.md)**: Explore the mathematical engine, including $EV$ calculation, Kelly Criterion sizing, Logistic Regression, and Monte Carlo simulations.
*   **[System Architecture](docs/ARCHITECTURE.md)**: Detailed topology of the "Cluster in a Box" design, Redis stream integration, and micro-agent specifications.
*   **[Developer Guide](docs/DEVELOPMENT.md)**: Step-by-step instructions for running backtests, monitoring real-time performance via the Dashboard, and extending agent functionality.

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
├── docs/                     # Deep-dive documentation wiki
├── config/                   # Global parameters & DB init scripts
├── data/                     # Persistent volumes (Postgres/Redis)
├── src/                      # Micro-agent source code
│   ├── analytics_engine/     # Mathematical modeling (SciPy/Sklearn)
│   ├── oracle_agent/         # Data scrapers & ingestion
│   ├── scout_agent/          # WebSocket market watchers
│   ├── sniper_agent/         # Order execution & paper logs
│   └── dashboard.py          # Real-time performance UI
└── tests/                    # Simulation & backtesting suite
```

---

## ─── License ───

Distributed under the MIT License. See `LICENSE` for more information.
