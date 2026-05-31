# Developer Guide: Project Sportsball

This guide explains how to extend, monitor, and validate the Sportsball's performance using the provided developer tooling. For day-to-day operation (first run, the modeling loop, resetting the DB, troubleshooting) see the **[Operations Runbook](OPERATIONS.md)**; for the database layout see **[SCHEMA.md](SCHEMA.md)**.

---

## 1. Local Environment Setup

To run tests, visualizations, and diagnostic tools on your host machine, initialize the local environment:

```bash
make setup
```
This creates a `./venv`, upgrades `pip`, and installs the `sportsball` package in editable mode with the `[tools]` extra (`matplotlib`, `pandas`, `nba_api`, `pytest`). After setup, `make test` runs the unit suite (154 tests). The suite uses in-memory fakes (`tests/fakes.py`) and needs no Redis/Postgres/network — and nothing on the default path imports `slack_sdk`. CI (`.github/workflows/ci.yml`) runs it on Python 3.11 + 3.12 on every push/PR.

---

## 2. Running Simulations

### Walk-Forward Backtest (Visual)
This performs a game-by-game simulation where the model learns and trades over your historical database.

```bash
make backtest-viz
```
*Output:* `data/plots/backtest_performance.png`

### Granular Pipeline Audit
Executes a technical backtest against a specific tick-data array (e.g., `tests/mock_ticks.json`).

```bash
make backtest
```

### Betting Backtest (skill vs. edge)
Walk-forward fractional-Kelly simulation on the holdout, reporting ROI / win% /
drawdown. Because the free data has no market odds, it brackets reality between a
naive (Elo-only) book and an efficient one (priced at our own model) — see
[WHITEPAPER §5.4](WHITEPAPER.md). Honest result: real skill vs a naive book, **no
edge vs an efficient one**.

```bash
make backtest-sim
```

---

## 3. Performance Monitoring

### CLI Dashboard
Sportsball includes a built-in real-time dashboard.

```bash
make dashboard
```

### Visual Analytics
Generate diagnostic charts based on live trade history:

*   **PnL Equity Curve:** `make plot`
*   **Model Calibration:** `make calibrate`

### Database Shell
Direct access to the PostgreSQL instance:

```bash
make shell
```

---

## 4. Quantitative Audit Tools

Sportsball provides professional tools to verify that your alpha is real:

### Closing Line Value (CLV)
Quantifies your edge by comparing your executed odds to the final market price.

```bash
make clv
```

### Professional Model Evaluation
Calculates Brier Score, Log-Loss, and RMSE to audit the model's predictive power.

```bash
make evaluate
```

### System Health
Real-time status check of the Redis broker and agent connectivity.

```bash
make health
```

---

## 5. Data Management & Enrichment

### Managed Historical Backfill
Populates the database with multi-sport, multi-season history from The Rundown.

```bash
docker exec agent_engine sportsball-backfill --managed
# or a single range:
docker exec agent_engine sportsball-backfill --start 2023-10-24 --end 2024-04-14 --sport 4
```

### Training the Model
Ensure the schema + history exist (`make bootstrap`), then optimize Elo params and
train the **v2** win-probability model the Engine loads (a `StandardScaler` +
logistic `Pipeline` over the 7-feature vector; writes `models/{win_prob_model.pkl,
team_state.json, model_meta.json}`). See [QUANT.md](QUANT.md) for the algorithm.

```bash
make optimize          # tunes K-factor + home-field advantage by log-loss
make train             # builds the 7-feature matrix + fits the Pipeline
# or both: make retrain

# Evaluate:
make eval-duckdb       # rigorous out-of-sample walk-forward holdout (no Postgres)
make backfill-signals  # persist model predictions (recent window) -> signals
make evaluate          # Brier/log-loss on those signals (in-sample sanity check)
```

### NBA Advanced Stats Fetcher
Pulls real-time Offensive/Defensive ratings and Pace using the `nba_api`.

```bash
make fetch-stats
```

### Free NBA history → DuckDB (offline research dataset)
For Moneyball-style analysis, two standalone scripts land decades of free
`nba_api` data into a portable `data/sportsball.duckdb` (no server, no API key).
This store is **parallel** to the Postgres model pipeline — see
[SCHEMA.md](SCHEMA.md#duckdb-analytics-store).

```bash
python scripts/ingest_nba_duckdb.py            # team results (~49K games, 40+ seasons)
python scripts/ingest_player_stats_duckdb.py   # player box scores (1,012,331 player-games, 3,584 players)
```

---

## 6. Extending the System

### Seeding Demo Data
To test visualizations before live history has accumulated:

```bash
make demo
```
This populates the database with 500 matched games and trades.

### Adding a New Oracle Scraper
1.  Add your API logic to `src/sportsball/agents/oracle.py` (or a new producer).
2.  Emit the `market_signal` schema via `build_signal(...)` — see docs/ARCHITECTURE.md §4.2.
3.  Rebuild and restart: `docker compose up -d --build oracle_agent`.

### Modifying the Math Engine
1.  Pure quant primitives live in `src/sportsball/quant/` (`odds.py`, `poisson.py`,
    `models.py`, `arbitrage.py`, `portfolio.py`).
2.  **Add a unit test** under `tests/` and run `make test` — the math layer is
    fully covered and has no DB/network dependencies.
3.  Run `make backtest` (and `make backtest-viz` once you have history) to check
    the PnL impact before rebuilding the image.

### Adding a Slack alert
1.  Add a **pure** Block Kit builder in `src/sportsball/notify/blocks.py`
    (returns `list[dict]`, no I/O) and a thin `Notifier` method in
    `notify/slack.py` that wraps `_post(...)`.
2.  Call it from the agent's `run()` after the relevant action, passing the
    notifier built via `build_notifier(settings)` (default `NULL_NOTIFIER` keeps
    it a no-op). **Never** let a Slack call raise into the agent — `_post`
    already swallows network errors; keep that contract.
3.  Test with the injected `FakeSlackClient` (`tests/fakes.py`): assert the
    builder output and that an unconfigured `Notifier` sends nothing. No network
    in tests.
