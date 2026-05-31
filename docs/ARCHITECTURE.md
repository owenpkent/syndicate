# System Architecture: Project Sportsball

Sportsball is designed as a **"Cluster in a Box"**—a distributed system of micro-agents orchestrated via Docker, optimized for low-latency market analysis and execution.

---

## 1. High-Level Topology

The system uses **Redis** as a high-speed message broker to decouple the agents. Communication is asynchronous and fire-and-forget:

*   **`market_signals`** — a Redis **List** used as a FIFO work queue. Producers (Oracle, Scout) `RPUSH` normalized signals; the Analytics Engine `LPOP`s them.
*   **`execution_signals`** — a Redis **List** the Analytics Engine pushes value/arbitrage orders onto; the Sniper Agent drains it.
*   **`active_trades`** — a Redis **Hash** (`market_id -> size`) holding live exposure, read by the Portfolio Risk Manager to enforce global limits.
*   **`pending_approval`** — a Redis **List** (+ companion **Hash** `pending_approval:store`) used by the optional [approval gate](#6-notifications--the-approval-gate). When enabled, high-EV signals land here instead of `execution_signals`, and the **Approver Agent** forwards only the human-approved ones onward.

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
                                         │       └───────┬───────┐
                  (gate enabled, high EV)│               │       ▼
                                         ▼               │  [ Sniper Agent ] ──► [ Execution Venue ]
                              ( pending_approval )        │       ▲
                                         ▼               │       │
                              [ Approver Agent ] ──Approve┴───────┘
                                  ▲      │ (Slack Socket Mode)
                                  └──────┘  Approve / Reject buttons
```

A separate **notification path** (see §6) is one-way: the Sniper, Settlement, and `health` tool emit alerts to Slack, and the `digest` tool posts a scheduled summary. None of these are on the trading hot path.

---

## 2. Agent Responsibilities

### Oracle Agent (The Ingester)
*   Responsible for polling external APIs (Sharp Books, Sports Data Providers).
*   Normalizes disparate data formats into a unified `market_signal` JSON schema.
*   Publishes to Redis.

### Scout Agent (The Watcher)
*   Discovers live markets via the Polymarket **Gamma API** (`markets/polymarket.py`)
    and subscribes to the **CLOB market channel** WebSocket for their token ids.
*   Parses `book` messages (best bid/ask) into implied probabilities and signals.
*   For **head-to-head** markets (two real competitors + a date in the slug) it now
    derives canonical identity — `event_id`, `matchup`, `participant` — via
    `markets/polymarket.parse_game_market`, so the Engine can **price** the market
    with the model (NBA today) and it keys like an Oracle signal. Futures/props
    (`Yes`/`No`) and undated markets fall back to an unpriced signal (Engine abstains).
*   Override discovery with `SCOUT_ASSET_IDS`. (Live socket behavior is verified
    against Polymarket's documented shapes but not exercised in CI — see §5.)

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
*   Emits a one-way Slack alert per graded trade when Slack is configured (§6).

### Approver Agent (The Gatekeeper) — *optional*
*   Runs **only** when the approval gate is enabled (`SLACK_REQUIRE_APPROVAL=true` + Socket Mode tokens). With no Slack config it does nothing and the Engine behaves exactly as before.
*   Drains `pending_approval`, posts an Approve/Reject Slack message, and stashes the message reference in `pending_approval:store`.
*   Receives button clicks over a **Slack Socket Mode** WebSocket (no public HTTP endpoint), and on **Approve** forwards the *unchanged* execution signal to `execution_signals`; on **Reject** discards it.
*   A TTL reaper auto-rejects (EXPIRED) suggestions nobody acted on — absence of a decision never trades.
*   Concurrency is lock-free: the posting loop and the socket callback share state only through Redis, and idempotency (double-click, poster/reaper race) is guaranteed by `broker.pop_pending` (only one caller's `HDEL` wins). See `src/sportsball/agents/approver.py`.

---

## 3. Infrastructure & Persistence

*   **Docker Orchestration:** Isolated runtimes for the agents (oracle, scout, engine, sniper, settlement, retrainer, and the optional approver) + 2 infrastructure services (Redis/Postgres), all from one image. The `digest` service is run-on-demand (cron).
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
        ─ OR, if the approval gate is on and EV ≥ threshold:
          RPUSH pending_approval; Approver posts to Slack; only Approve
          forwards the same signal to execution_signals (see §6).
7. Sniper BRPOPLPUSH execution_signals → in-flight
     a. PAPER mode: simulate slippage  →  record_trade(status='OPEN', market_id)
     b. HSET active_trades[market_id] = size
     c. notify_fill(...) → Slack (one-way, no-op if unconfigured)
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

**Addressed in v0.3**

* ✅ **Live Polymarket discovery.** The Scout resolves real CLOB token ids via the
  Gamma API and subscribes to the live market channel (`markets/polymarket.py`,
  `agents/scout.py`), parsing real `book` messages.
* ✅ **Canonical event ids.** `matching.canonical_event_id` derives a
  venue-independent id from (sport, date, teams) so the Oracle, backfill, and the
  free NBA ingester all collapse the same game onto one `event_id` — the
  mechanism that makes cross-venue settlement and arbitrage possible.
* ✅ **Free training data.** `sportsball-ingest-nba` pulls every regular-season
  NBA result from `nba_api` (no key) into `events`, so the model trains on
  thousands of real games rather than a thin slate.
* ✅ **Automated retraining.** The Retrainer agent runs optimize→train on a
  schedule and the Engine hot-reloads the new model on its next loop iteration.

**Addressed in v0.4**

* ✅ **Slack integration.** One-way alerts (fills, settlements, degraded health),
  a scheduled `digest`, and an optional human-in-the-loop **approval gate** over
  Slack Socket Mode — all off by default, all isolated from the trading hot path.
  See §6.
* ✅ **Deeper free data → DuckDB.** Beyond team results, `scripts/ingest_nba_duckdb.py`
  and `scripts/ingest_player_stats_duckdb.py` land 40+ seasons of team box scores
  and ~1.01M player game logs (1,012,331 across 3,584 players) in a portable DuckDB file for Moneyball-style analysis
  (see [SCHEMA.md](SCHEMA.md#duckdb-analytics-store)). This store is currently
  parallel to the Postgres model pipeline, not yet wired into training.

**Still open / caveats**

1. **Live integrations: unit-tested + manually smoke-tested, not in CI.** Parsing
   is unit-tested against documented shapes, and `make smoke` validates against
   the *real* services (a reference run returned live Gamma markets, 1230 nba_api
   games for a full season, and a connected CLOB socket). They are not yet
   exercised in automated CI — run `make smoke` after dependency/API changes.
2. **Cross-venue arbitrage needs parseable sports markets.** The Scout now
   extracts (date, teams) from head-to-head Gamma markets and the Engine prices
   them, so the Polymarket path is no longer inert. Polymarket still doesn't
   expose home/away (the Scout adopts `outcomes[0]=away, [1]=home`), but
   orientation no longer breaks the arbitrage book: it is keyed by
   `matching.matchup_key` (the sorted-token, order-independent matchup key) with
   outcomes tracked by team token, so an Oracle line and a reversed-orientation
   Polymarket line for the same game now meet and compare. The remaining edge case
   is *settling* a reversed-orientation venue's own `event_id` against the
   canonical (correctly-oriented) result row.

Contributions that close these are welcome.

---

## 6. Notifications & the approval gate

Slack support lives in `src/sportsball/notify/` and is **entirely optional** —
with no `SLACK_*` environment variables the notifier is a no-op and the gate is
disabled, so the pipeline behaves byte-for-byte as it did before. Configuration
is a single `SlackConfig` dataclass (`config.py`), mirroring the `rundown_api_key`
precedent. Operational setup is in [OPERATIONS.md §7](OPERATIONS.md#7-slack-integration).

**Layering** (each inner layer is pure / testable in isolation):

| Module | Role |
|--------|------|
| `notify/blocks.py` | Pure Block Kit message builders (no `slack_sdk` import, no I/O) |
| `notify/slack.py` | `Notifier` — sends; **no-op when unconfigured** and **never raises** into a caller (network errors are caught + logged, tokens never logged). Lazy `slack_sdk` import. |
| `notify/gate.py` | `ApprovalGate` — routing only (no network): `should_gate(ev)` + `enqueue()` into Redis |
| `agents/approver.py` | The long-running agent: poster loop + Socket Mode handler + TTL reaper |
| `tools/digest.py` | `sportsball-digest` — a one-shot trailing-24h summary card |

**Design invariants**

* **Hot-path isolation.** The Engine never calls Slack; it only enqueues to
  Redis. Notifier failures are swallowed so a Slack outage can't break the
  Sniper, Settlement, or Engine loops.
* **Two transports.** One-way alerts work with either a bot token *or* an
  incoming webhook. The interactive gate requires a bot token **and** an
  app-level token (Socket Mode) — Socket Mode is what lets buttons round-trip
  without exposing a public endpoint, the right fit for the single-box design.
* **Idempotency & fail-safe.** `broker.pop_pending` is the single-remover
  primitive (HDEL-wins) that makes double-clicks and poster/reaper races
  harmless; unactioned suggestions auto-expire (never trade).
* **Mode unchanged.** The gate changes *routing* only; `EXECUTION_MODE` stays
  `PAPER`.
