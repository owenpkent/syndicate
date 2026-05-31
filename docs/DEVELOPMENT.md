# Developer Guide: Project Syndicate

This guide explains how to extend, monitor, and validate the Syndicate's performance.

---

## 1. Running Backtests

The backtesting engine allows you to simulate your strategy against historical data.

### Execution
Run the backtest within the `analytics_engine` container to ensure the mathematical environment is identical to production:

```bash
docker exec agent_engine python tests/backtest_pipeline.py --input tests/mock_ticks.json --bankroll 1000 --kelly 0.25
```

### Data Format
Input files (JSON) should follow this structure:
```json
[
  {
    "market_id": "NBA-001",
    "odds": 2.10,
    "true_prob": 0.55,
    "outcome": 1
  }
]
```

---

## 2. Real-Time Performance Monitoring

### CLI Dashboard
Syndicate includes a built-in real-time dashboard that queries the `trade_history` database.

View the live feed:
```bash
docker compose logs -f dashboard
```

### Manual Database Queries
You can query the Postgres database directly to perform custom analysis:

```bash
docker exec syndicate_db psql -U syndicate_admin -d market_history -c "SELECT * FROM trade_history;"
```

---

## 3. Extending the System

### Adding a New Oracle Scraper
1.  Add your API logic to `src/oracle_agent/main.py`.
2.  Ensure your data is normalized to the `market_signal` schema.
3.  Restart the agent: `docker compose restart oracle_agent`.

### Modifying the Math Engine
1.  All core math utilities are housed in `src/analytics_engine/math_utils.py` and `advanced_models.py`.
2.  Because source code is volume-mounted in development mode, changes take effect upon agent restart.
3.  **Always** run the backtest after modifying the math core to verify expected PnL impact.
