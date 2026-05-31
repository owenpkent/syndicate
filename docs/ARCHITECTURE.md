# System Architecture: Project Sportsball

Sportsball is designed as a **"Cluster in a Box"**—a distributed system of micro-agents orchestrated via Docker, optimized for low-latency market analysis and execution.

---

## 1. High-Level Topology

The system uses **Redis** as a high-speed message broker (Streams/PubSub) to facilitate asynchronous communication between agents.

```text
[ Data Sources ]
      │
      ▼
[ Oracle Agent ]  ──( market_signals )──► [ Analytics Engine ] ──► [ Postgres DB ]
[ Scout Agent  ]  ──( market_signals )──► [ Analytics Engine ]
                                                 │
                                         ┌───────┴───────┐
                                ( execution_signals ) ( ARB_signals )
                                         └───────┬───────┘
                                                 ▼
                                          [ Sniper Agent ] ──► [ Execution Venue ]
```

---

## 2. Agent Responsibilities

### Oracle Agent (The Ingester)
*   Responsible for polling external APIs (Sharp Books, Sports Data Providers).
*   Normalizes disparate data formats into a unified `market_signal` JSON schema.
*   Publishes to Redis.

### Scout Agent (The Watcher)
*   Maintains low-latency WebSocket connections to decentralized order books (e.g., Polymarket).
*   Tracks liquidity and bid/ask spreads in real-time.
*   Translates order book mid-prices into implied probabilities.

### Analytics Engine (The Brain)
*   Subscribes to all `market_signals`.
*   **EV Strategy:** Applies statistical models (Regression, Poisson, MC) to determine $P_{\text{true}}$.
*   **Arbitrage Strategy:** Maintains a cross-venue order book to detect risk-free discrepancies.
*   Calculates $EV$ and optimal $f^*$ (Kelly size).
*   Logs every signal to **PostgreSQL** for historical analysis.

### Sniper Agent (The Executioner)
*   Subscribes to `execution_signals`.
*   Performs final safety checks (Slippage tolerance).
*   Signs transactions or logs "Paper Trades" for performance tracking.

---

## 3. Infrastructure & Isolation

*   **Docker Orchestration:** Containers ensure zero dependency cross-contamination. Each agent has its own isolated runtime and environment variables.
*   **Networking:** All internal traffic happens on a private Docker bridge network. Only the Broker and Database expose ports to the host machine for monitoring.
*   **Persistence:**
    *   **Postgres:** Long-term storage for trade history, market movements, and model performance.
    *   **Redis:** Volatile, high-speed RAM storage for the active signal pipeline.
