# Developer Guide: Project Sportsball

This guide explains how to extend, monitor, and validate the Sportsball's performance using the provided developer tooling.

---

## 1. Local Environment Setup

To run visualizations and diagnostic tools on your host machine, you must initialize the local environment:

```bash
make setup
```
This creates a `./venv`, upgrades `pip`, and installs all host-side dependencies (`psycopg2`, `matplotlib`, `scikit-learn`).

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
Once history is backfilled, optimize Elo params then train the win-probability
model the Engine loads (writes to `models/`):

```bash
make optimize   # tunes K-factor + home-field advantage by log-loss
make train      # fits logistic regression on the Elo walk-forward
```

### NBA Advanced Stats Fetcher
Pulls real-time Offensive/Defensive ratings and Pace using the `nba_api`.

```bash
make fetch-stats
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
