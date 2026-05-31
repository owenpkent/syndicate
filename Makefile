# Project Syndicate Tooling

PYTHON=./venv/bin/python3
PIP=./venv/bin/pip

.PHONY: setup dashboard plot calibrate backtest shell

setup:
	@echo "Setting up local virtual environment..."
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -r scripts/requirements.txt
	@echo "Setup complete. Use 'make dashboard' or 'make plot'."

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

backtest:
	@echo "Running containerized backtest..."
	docker exec agent_engine python tests/backtest_pipeline.py --input tests/mock_ticks.json --bankroll 1000 --kelly 0.25

shell:
	@echo "Entering Postgres Shell..."
	docker exec -it syndicate_db psql -U syndicate_admin -d market_history
