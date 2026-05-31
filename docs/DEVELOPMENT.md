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

## 4. Extending the System

### Seeding Demo Data
To test visualizations before live history has accumulated:

```bash
make demo
```
This populates the database with 500 matched games and trades.

### Adding a New Oracle Scraper
1.  Add your API logic to `src/oracle_agent/main.py`.
2.  Ensure your data is normalized to the `market_signal` schema.
3.  Restart the agent: `docker compose restart oracle_agent`.

### Modifying the Math Engine
1.  Core math utilities are in `src/analytics_engine/math_utils.py` and `advanced_models.py`.
2.  Changes take effect immediately on agent restart due to Docker volume mounts.
3.  **Always** run `make backtest-viz` after modifying the math core to verify the PnL impact.
