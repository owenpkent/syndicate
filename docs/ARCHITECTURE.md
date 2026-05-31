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
*   Joins open `trades` to FINAL `events` on `event_id` (a foreign-key join).
*   Grades each trade by `side`, writing `status` (`WIN`/`LOSS`) and realized `pnl`.
*   Clears the settled position's exposure from `active_trades` (the reaper).
*   Provides the "Truth Loop" required for accurate visual performance tracking.

---

## 3. Infrastructure & Persistence

*   **Docker Orchestration:** Isolated runtimes for all 5 agents + 2 infrastructure services (Redis/Postgres).
*   **Persistence Layer (PostgreSQL):** normalized around `event_id` foreign
    keys — see [SCHEMA.md](SCHEMA.md) for the full model.
    *   `events`: one row per game (SCHEDULED stub → FINAL with scores + closing lines).
    *   `signals`: every modeled evaluation (EV log), FK → `events`.
    *   `trades`: executed paper positions + realized PnL, FK → `events`.
    *   `team_advanced_stats`: feature storage for model enrichment.
    *   All SQL is centralized in the repository layer (`sportsball.store`).
*   **Broker (Redis):** Orchestrates the high-speed signal pipeline via List-based queues and maintains real-time portfolio exposure state.

---

## 4. Signal Lifecycle & Schemas

### 4.1 End-to-end path of one signal

```text
1. Oracle/Scout fetch a price          →  build a market_signal dict
2. RPUSH market_signals (JSON)
3. Engine BRPOPLPUSH market_signals → in-flight  (reliable: ack after step 6)
4. Engine computes P_true              →  trained ModelBundle + Elo + net-rating
                                          (no model → ABSTAIN: log, never trade)
5. Engine upsert_event_stub + record_signal(event_id, side, …)   →  events, signals
6. If EV > safety_buffer_ev:
     a. Kelly sizes the bet            →  f = multiplier * EV/(odds-1)
     b. PortfolioRiskManager clamps it →  global-exposure + correlation guards
     c. RPUSH execution_signals  (carries event_id, side, teams — resolved once)
7. Sniper BRPOPLPUSH execution_signals → in-flight
     a. PAPER mode: simulate slippage  →  record_trade(status='OPEN', market_id)
     b. HSET active_trades[market_id] = size
8. Backfill/results feed               →  upsert_event_result(status='FINAL', scores, closes)
9. Settlement (every 60s):
     trades ⋈ events ON event_id (FINAL)  →  set status WIN/LOSS + pnl + settled_ts
     HDEL active_trades[market_id]         →  exposure reaper
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

> **Why `market_id` format matters:** producers must emit `SOURCE-EVENTID-TEAM`.
> The Engine parses it **once** (`store.parse_market_id`) to derive the
> `event_id` and resolve `side`, then stamps both onto the execution signal — so
> downstream agents and analytics use foreign-key joins on `event_id`, not
> substring matching.

### 4.3 PostgreSQL tables

Full column-level model in [SCHEMA.md](SCHEMA.md).

| Table | Written by | Read by | Purpose |
|-------|-----------|---------|---------|
| `events` | Engine (stub), backfill/seed (result) | settlement, trainer, optimizer, CLV, dashboard | One row per game: teams, status, scores, closing lines |
| `signals` | Analytics Engine | `evaluate`, `visualize_calibration` | Every modeled prediction + EV (FK → events) |
| `trades` | Sniper, Settlement | dashboard, `clv`, `visualize_pnl` | Executed paper positions + realized PnL (FK → events) |
| `team_advanced_stats` | `fetch_nba_stats` | Analytics Engine (enrichment) | Off/Def/Net rating + pace per team |

---

## 5. Known Limitations

This is a **paper-trading research and demonstration environment**, not a
live-capital trading system. The v0.2 overhaul closed several of the original
weaknesses; the rest are tracked as the roadmap.

**Resolved in v0.2**

* ✅ **No more trading on noise.** The Oracle no longer invents a `true_prob`;
  the Engine derives probability only from a trained `ModelBundle` and
  **abstains** (logs, never stakes) when no model is loaded
  (`strategy.require_model`).
* ✅ **Reliable queue.** Consumers use `BRPOPLPUSH` into a per-consumer in-flight
  list with explicit `ack`, so a crash mid-processing recovers the message
  instead of dropping it (`sportsball.broker.Broker.reliable_consume`).
* ✅ **Exposure reaper.** The Settlement Agent clears a position's entry from the
  `active_trades` hash when it settles, so the global-exposure guard no longer
  silently ratchets shut.
* ✅ **Real health check.** `sportsball-health` probes Redis/Postgres and reports
  queue depth, exposure, and row counts (and exits non-zero when degraded)
  instead of always printing `[OK]`.
* ✅ **One image, no duplication.** A single package + Docker image replaced the
  five copy-pasted agents and the 14 hardcoded DB-credential blocks.
* ✅ **Normalized schema, real joins.** `events`/`signals`/`trades` with
  `event_id` foreign keys replaced the fragile `LIKE '%' || event_id || '%'`
  substring matching. All SQL lives in one repository layer (`sportsball.store`).

**Still open (roadmap — Phase 3)**

1. **Scout uses placeholder subscriptions.** `assets_ids: ["123456","789012"]`
   are not real Polymarket markets. Resolve live `asset_ids` via the Gamma API
   (override today with `SCOUT_ASSET_IDS`).
2. **Cross-venue arbitrage is sim-only.** The book fills both legs only when two
   venues publish the same `event_id` with `Home`/`Away` types; the live Scout
   doesn't emit that shape yet, so real cross-venue arbs await live data.
3. **No automated retraining loop.** The modeling pipeline (optimize → train) is
   run manually; a scheduled retrain on fresh backfill would keep ratings current.

Contributions that close any of these are welcome.
