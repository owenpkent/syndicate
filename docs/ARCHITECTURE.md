# System Architecture: Project Sportsball

Sportsball is designed as a **"Cluster in a Box"**—a distributed system of micro-agents orchestrated via Docker, optimized for low-latency market analysis and execution.

---

## 1. High-Level Topology

The system uses **Redis** as a high-speed message broker to decouple the agents. Communication is asynchronous and fire-and-forget:

*   **`market_signals`** — a Redis **List** used as a FIFO work queue. Producers (Oracle, Scout) `RPUSH` normalized signals; the Analytics Engine `LPOP`s them.
*   **`execution_signals`** — a Redis **List** the Analytics Engine pushes value/arbitrage orders onto; the Sniper Agent drains it.
*   **`active_trades`** — a Redis **Hash** (`market_id -> size`) holding live exposure, read by the Portfolio Risk Manager to enforce global limits.

> **Note:** This is a List-based queue design, not Redis Streams or Pub/Sub. It is deliberately simple — there are no consumer groups, acks, or replay. A signal is processed by exactly one consumer and then gone. See [Known Limitations](#5-known-limitations) for the trade-offs.

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
*   Pushes success trades to `active_trades` Redis hash for real-time risk coordination.

### Settlement Agent (The Accountant)
*   Monitors `trade_history` for non-finalized trades.
*   Matches trades with `historical_results` once outcomes are available.
*   Calculates real PnL and updates trade status to `WIN` or `LOSS`.
*   Provides the "Truth Loop" required for accurate visual performance tracking.

---

## 3. Infrastructure & Persistence

*   **Docker Orchestration:** Isolated runtimes for all 5 agents + 2 infrastructure services (Redis/Postgres).
*   **Persistence Layer (PostgreSQL):**
    *   `historical_results`: Multi-season repository of past outcomes and closing lines.
    *   `market_history`: Real-time log of every processed model prediction and $EV$ signal.
    *   `trade_history`: Immutable ledger of executed positions and final settlements.
    *   `team_advanced_stats`: Real-time feature storage for model enrichment.
*   **Broker (Redis):** Orchestrates the high-speed signal pipeline via List-based queues and maintains real-time portfolio exposure state.

---

## 4. Signal Lifecycle & Schemas

### 4.1 End-to-end path of one signal

```text
1. Oracle/Scout fetch a price        →  build a market_signal dict
2. RPUSH market_signals (JSON)        →  Redis List
3. Engine LPOP market_signals
4. Engine computes P_true             →  trained model + Elo + net-rating enrichment
5. Engine computes EV = P_true*odds-1 →  INSERT into market_history (always logged)
6. If EV > safety_buffer_ev:
     a. Kelly sizes the bet           →  f = multiplier * EV/(odds-1)
     b. PortfolioRiskManager clamps it →  global-exposure + correlation guards
     c. RPUSH execution_signals
7. Sniper LPOP execution_signals
     a. PAPER mode: simulate slippage  →  INSERT trade_history (SUCCESS/FAILED)
     b. HSET active_trades market_id size
8. Settlement Agent (every 60s):
     JOIN trade_history ↔ historical_results → UPDATE status to WIN/LOSS
```

Arbitrage takes a parallel branch in step 6: the `ArbitrageEngine` maintains a
cross-venue book keyed by event id and, when `Σ(1/oddsᵢ) < 1`, emits an
`ARBITRAGE`-typed execution signal with one leg per outcome.

### 4.2 `market_signal` schema (the contract between producers and the Engine)

Every Oracle/Scout producer **must** emit this shape onto `market_signals`:

```jsonc
{
  "market_id": "RUNDOWN-<event_id>-<participant>",  // SOURCE-EVENTID-TEAM; the Engine/Settlement parse on "-"
  "odds":      1.91,                                 // decimal odds
  "true_prob": 0.55,                                 // optional; consumed only if the Engine can't model it
  "metadata": {
    "source":      "The Rundown",                    // venue label, used by the arbitrage book
    "matchup":     "Lakers @ Celtics",               // "<away> @ <home>" — split on " @ " for Elo lookup
    "participant": "Celtics"                          // which side this price is for
  }
}
```

> **Why `market_id` format matters:** the Engine, Settlement Agent, CLV, and
> evaluation scripts all recover the event id and team by splitting on `-`.
> Producers that don't follow `SOURCE-EVENTID-TEAM` will be logged but never
> settled or enriched. This string-coupling is a known weakness (see §5).

### 4.3 PostgreSQL tables

| Table | Written by | Read by | Purpose |
|-------|-----------|---------|---------|
| `market_history` | Analytics Engine | dashboard, `evaluate_stats`, `visualize_calibration` | Every prediction + EV, whether or not it was traded |
| `trade_history` | Sniper Agent, Settlement Agent | dashboard, `analyze_clv`, `visualize_pnl` | Executed paper trades and their final settled status |
| `historical_results` | `historical_scraper`, `seed_demo_data` | Settlement, trainer, optimizer, CLV | Final scores + closing lines — the "truth" table |
| `team_advanced_stats` | `fetch_nba_stats` | Analytics Engine (enrichment) | Off/Def/Net rating + pace per team |

---

## 5. Known Limitations

This is a **paper-trading research and demonstration environment**, not a
live-capital trading system. The design makes several deliberate (and a few
incidental) trade-offs that any contributor should understand before extending it:

1. **Probabilities are not yet a real edge.** When no trained model is loaded,
   the Oracle fills `true_prob` with `random.uniform(0.45, 0.60)`. EV computed
   from a random probability is noise. Real alpha requires a trained model
   (`model_trainer.py`) **and** a calibrated feature pipeline.
2. **String-coupled identifiers.** Event/team identity is recovered by splitting
   `market_id` on `-` and `LIKE '%' || event_id || '%'` joins. This is fragile
   (a numeric event id like `1` matches many rows) and `O(n²)`. A normalized
   `events` table with foreign keys is the correct fix.
3. **List queue, not Streams.** No consumer groups, acks, or replay — a crash
   mid-processing drops the in-flight signal. Fine for simulation; insufficient
   for production.
4. **`active_trades` never expires.** The Sniper accumulates exposure in the
   `active_trades` hash with no settlement-driven cleanup, so the global-exposure
   guard eventually rejects everything. A reaper keyed to settled trades is needed.
5. **Scout uses placeholder subscriptions.** `assets_ids: ["123456", "789012"]`
   are not real Polymarket markets, and the message schema is illustrative. The
   Scout connects but will not produce live signals without real asset ids.
6. **Single-source arbitrage.** The arbitrage book only fills both legs when two
   venues publish the **same** `event_id` with `Home`/`Away` participant types.
   The Scout (Polymarket) does not currently emit that shape, so cross-venue arbs
   will not trigger end-to-end yet.

These are tracked as the project roadmap; contributions that close any of them
are welcome.
