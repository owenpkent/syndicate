# Project Sportsball — host-side tooling
#
# Agents run in Docker (see docker-compose.yml). These targets cover local
# setup, tests, visualizations, and operator tools. Host commands point at
# localhost; set DB_HOST/REDIS_HOST in the environment to target a remote.

PYTHON=./venv/bin/python3
PIP=$(PYTHON) -m pip

.PHONY: setup test dashboard webui health digest smoke plot calibrate clv evaluate fetch-stats demo \
        backtest backtest-viz optimize train retrain bootstrap ingest-nba backfill-signals \
        player-strength roster-pit ingest-injuries ingest-odds dryrun measure-algos \
        eval-duckdb measure-features model-quality backtest-sim shell

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

# Web dashboard (FastAPI). Auto data source: Postgres -> DuckDB -> demo. Needs the
# web extra (pip install -e ".[web]"). MODE=demo|duckdb|postgres, PORT=8000.
webui:
	@$(PYTHON) -m sportsball.web.app --mode $(or $(MODE),auto) --port $(or $(PORT),8000)

health:
	@echo "Checking system health..."
	@REDIS_HOST=localhost DB_HOST=localhost $(PYTHON) -m sportsball.tools.health

# Post the trailing-24h performance digest to Slack (no-op without SLACK_*).
digest:
	@REDIS_HOST=localhost DB_HOST=localhost $(PYTHON) -m sportsball.tools.digest

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

# Ensure the Postgres schema exists (idempotent) + load DuckDB history -> events.
bootstrap:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.bootstrap

# Free, no-API-key historical results from nba_api -> events (training data).
ingest-nba:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.ingest_nba

# Persist the trained model's predictions as signals so `make evaluate` scores it.
backfill-signals:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.backfill_signals

# Roster strength from the DuckDB player logs -> team_advanced_stats.player_strength.
player-strength:
	@DB_HOST=localhost $(PYTHON) scripts/compute_player_strength.py

# Point-in-time (season-to-date) roster strength per team-game -> team_strength_pit.
roster-pit:
	@DB_HOST=localhost $(PYTHON) scripts/precompute_roster_pit.py

# Point-in-time roster availability per team-game (the injuries lever) ->
# team_availability_pit. Feeds the model's availability_diff feature.
ingest-injuries:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.ingest_injuries

# Offline end-to-end dry run on a SYNTHETIC season (no data/network/DB needed):
# exercises the real walk_forward + holdout + backtest + odds-ingest code and
# reports the availability feature's lift. Needs the duckdb extra.
dryrun:
	@$(PYTHON) scripts/offline_dryrun.py

# Measure the v4 algorithm changes (feature lift, ensemble, calibration,
# uncertainty-Kelly) out-of-sample on a synthetic season. No data needed.
measure-algos:
	@$(PYTHON) scripts/measure_algorithms.py

# Closing odds (real lines) -> events.home_close/away_close, unblocking real CLV.
# FILE=path for an offline JSON/CSV feed, or set ODDS_API_KEY for The Odds API.
ingest-odds:
	@DB_HOST=localhost $(PYTHON) -m sportsball.pipelines.ingest_odds $(if $(FILE),--file $(FILE),)

# Train + out-of-sample (chronological holdout) eval against the DuckDB store,
# no Postgres needed. Add WRITE=1 to also persist Engine-loadable artifacts.
eval-duckdb:
	@$(PYTHON) scripts/train_eval_duckdb.py $(if $(WRITE),--write,)

# Out-of-sample feature ablation (needs Postgres team_advanced_stats for enrichment).
measure-features:
	@DB_HOST=localhost $(PYTHON) scripts/measure_features.py

# Calibration (ECE/reliability) + Elo/feature hyperparameter sweep.
model-quality:
	@DB_HOST=localhost $(PYTHON) scripts/model_quality.py

# Walk-forward betting backtest: ROI/win-rate/drawdown vs a synthetic market + vig.
# ANALYZE=1 adds per-season ROI, an EV-buffer sweep, and odds-bucket breakdowns.
backtest-sim:
	@DB_HOST=localhost $(PYTHON) scripts/backtest.py $(if $(ANALYZE),--analyze,)

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
