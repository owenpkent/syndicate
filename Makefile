# Project Sportsball — host-side tooling
#
# Agents run in Docker (see docker-compose.yml). These targets cover local
# setup, tests, visualizations, and operator tools. Host commands point at
# localhost; set DB_HOST/REDIS_HOST in the environment to target a remote.

PYTHON=./venv/bin/python3
PIP=$(PYTHON) -m pip

.PHONY: setup test dashboard health smoke plot calibrate clv evaluate fetch-stats demo \
        backtest backtest-viz optimize train retrain ingest-nba shell

setup:
	@echo "Setting up local virtual environment..."
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[tools]"
	@echo "Setup complete. Try 'make test' or 'make dashboard'."

test:
	@echo "Running unit test suite..."
	$(PYTHON) -m pytest tests/ -v

dashboard:
	@echo "Launching real-time dashboard..."
	@DB_HOST=localhost $(PYTHON) -m sportsball.tools.dashboard

health:
	@echo "Checking system health..."
	@REDIS_HOST=localhost DB_HOST=localhost $(PYTHON) -m sportsball.tools.health

# Validate the live external integrations (Gamma API, nba_api, CLOB WebSocket).
smoke:
	@$(PYTHON) -m sportsball.tools.smoke

# --- Modeling pipelines (host, against localhost Postgres) ---
optimize:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.optimize

train:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.train

retrain:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.retrain

# Free, no-API-key historical results from nba_api -> events (training data).
ingest-nba:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.ingest_nba

# --- Visualizations & analysis (legacy scripts, Phase 2 will port these) ---
plot:
	@DB_HOST=localhost $(PYTHON) scripts/visualize_pnl.py
	@echo "Output saved to data/plots/pnl_curve.png"

calibrate:
	@DB_HOST=localhost $(PYTHON) scripts/visualize_calibration.py
	@echo "Output saved to data/plots/calibration_plot.png"

clv:
	@DB_HOST=localhost $(PYTHON) -m sportsball.tools.clv

evaluate:
	@DB_HOST=localhost $(PYTHON) -m sportsball.tools.evaluate

fetch-stats:
	@DB_HOST=localhost $(PYTHON) scripts/fetch_nba_stats.py

demo:
	@echo "Seeding demo data for visualization..."
	@DB_HOST=localhost $(PYTHON) scripts/seed_demo_data.py

backtest-viz:
	@DB_HOST=localhost $(PYTHON) scripts/visualize_backtest.py
	@echo "Chart saved to data/plots/backtest_performance.png"

backtest:
	@echo "Running backtest over tests/mock_ticks.json..."
	$(PYTHON) tests/backtest_pipeline.py --input tests/mock_ticks.json --bankroll 1000 --kelly 0.25

shell:
	@echo "Entering Postgres shell..."
	docker exec -it sportsball_db psql -U sportsball_admin -d market_history
