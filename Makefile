# Project Sportsball Tooling

PYTHON=./venv/bin/python3
PIP=$(PYTHON) -m pip

.PHONY: setup test dashboard plot calibrate backtest shell

setup:
	@echo "Setting up local virtual environment..."
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -r scripts/requirements.txt
	@echo "Setup complete. Use 'make dashboard' or 'make plot'."

test:
	@echo "Running unit test suite..."
	$(PYTHON) -m pytest tests/ -v

dashboard:
	@echo "Launching real-time dashboard..."
	@export DB_HOST=localhost && $(PYTHON) src/dashboard.py

plot:
	@echo "Generating PnL Equity Curve..."
	@export DB_HOST=localhost && $(PYTHON) scripts/visualize_pnl.py
	@echo "Output saved to data/plots/pnl_curve.png"

calibrate:
	@echo "Generating Model Calibration Plot..."
	@export DB_HOST=localhost && $(PYTHON) scripts/visualize_calibration.py
	@echo "Output saved to data/plots/calibration_plot.png"

clv:
	@echo "Analyzing Closing Line Value (CLV)..."
	@export DB_HOST=localhost && $(PYTHON) scripts/analyze_clv.py

evaluate:
	@echo "Calculating professional model metrics..."
	@export DB_HOST=localhost && $(PYTHON) scripts/evaluate_stats.py

health:
	@echo "Checking system health..."
	@export REDIS_HOST=localhost && $(PYTHON) scripts/monitor_agents.py

fetch-stats:
	@echo "Fetching NBA Advanced stats..."
	@export DB_HOST=localhost && $(PYTHON) scripts/fetch_nba_stats.py

demo:
	@echo "Seeding demo data for visualization..."
	@export DB_HOST=localhost && $(PYTHON) scripts/seed_demo_data.py

backtest-viz:
	@echo "Generating Historical Performance Visualization..."
	@export DB_HOST=localhost && $(PYTHON) scripts/visualize_backtest.py
	@echo "Chart saved to data/plots/backtest_performance.png"

backtest:
	@echo "Running containerized backtest..."
	docker exec agent_engine python tests/backtest_pipeline.py --input tests/mock_ticks.json --bankroll 1000 --kelly 0.25

shell:
	@echo "Entering Postgres Shell..."
	docker exec -it sportsball_db psql -U sportsball_admin -d market_history
