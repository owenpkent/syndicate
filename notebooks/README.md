# notebooks/ — prediction visualization

Jupyter notebooks for *seeing* how predictions behave — the visual companion to
the headless tools (`make calibrate`, `make clv`) and the DeFi collectors.

## Setup

```bash
make setup                                   # base env
./venv/bin/python3 -m pip install -e ".[notebook]"   # jupyterlab, ipykernel, seaborn, plotly
./venv/bin/python3 -m ipykernel install --user --name sportsball --display-name "sportsball (venv)"
./venv/bin/jupyter lab                        # then pick the "sportsball (venv)" kernel
```

## Notebooks

| Notebook | Reads | Shows |
|---|---|---|
| `01_defi_explore.ipynb` | `data/defi.duckdb` | HL↔CEX basis & lead-lag on backfilled 1-min candles, funding over time, funding/OI cross-section, and a basis→next-move baseline. Uses `backfill_history.py` history (populated now). |
| `02_model_eval.ipynb` | `data/sportsball.duckdb` | Offline walk-forward holdout of the v4 win-prob model — reliability/calibration curve, accuracy by confidence bucket, model-vs-market (CLV proxy). No Postgres needed. |
| `03_polymarket_eval.ipynb` | `data/defi.duckdb` | Scores Polymarket crypto mids against settled outcomes (`pm_resolved`) — Brier + calibration, using the mid ~24h pre-close as the prediction. Populated from backfilled resolved markets. |
| `04_model_predictions.ipynb` | `data/sportsball.duckdb` | Per-game view of what *each* learner predicts — logistic vs GBT vs ensemble vs market — with the actual outcome. Dot plot of recent games, where-they-disagree scatter, and who-wins-the-disagreements analysis. Out-of-sample, mirrors `pipelines/train.py`. |
| `05_ensemble_weight_sweep.ipynb` | `data/sportsball.duckdb` | Finds the best logistic↔GBT blend weight honestly (train/val/test 3-way split: pick weight on validation, report on test). Validation curve + unbiased test table + calibration/ECE check. Result: GBT-dominant (shipped 0.75) beats the old 50/50 on accuracy, log-loss, and calibration. |
| `06_model_comms.ipynb` | `data/sportsball.duckdb` | Four ways to *communicate* the prediction data: cumulative-accuracy race, rolling logistic↔GBT divergence, predicted-probability distribution (why GBT wins), and an interactive plotly per-game explorer. |
| `07_mlb_model.ipynb` | `data/mlb.duckdb` | Baseball win-prob model: the sport-agnostic Elo+feature pipeline on 38k real MLB games (`research/mlb/ingest_mlb.py`). Holdout accuracy/log-loss/calibration, Elo ratings, skill-over-time. Honest ~56% (ceiling = starting pitcher). |
| `08_world_cup_model.ipynb` | `data/wc.duckdb` | World Cup / international football: 3-class (W/D/L) Elo model on 49k matches (`research/wc/`). Overall vs World-Cup-specific holdout (~51% 3-way), top-team Elo, and 2026 fixture predictions. |
| `09_nhl_model.ipynb` | `data/nhl.duckdb` | NHL hockey win-prob: the binary Elo+feature pipeline on 18k games (`research/nhl/`). Holdout accuracy/log-loss/calibration, Elo ratings, skill-over-time. Honest ~56% (ceiling = starting goalie). |
| `10_crypto_timeseries.ipynb` | `data/defi.duckdb` | What's predictable in crypto: Fourier spectra + ACF + OOS predictability on BTC 1-min. Direction ~50% / R²≈0 (efficient), but volatility R²≈0.42 and volume is 24h-seasonal. Fourier as a diagnostic, not a predictor. |
| `11_polymarket_calibration.ipynb` | `data/defi.duckdb` | Is Polymarket calibrated across categories? 1.5k resolved markets (`research/defi/ingest_polymarket_resolved.py`): overall reliability + favorite-longshot bins + per-category Brier (Sports sharpest 0.13; Crypto highest 0.25 = uncertainty, not mispricing). Broadly efficient. |
| `12_funding_carry.ipynb` | `data/defi.duckdb` | Funding-carry tracker: ranks Hyperliquid perps by *persistent* carry (mean funding × sign-stability) vs transient spikes, over 30d hourly `hl_funding_hist`. Separates real delta-neutral yield (e.g. XMR ~41%/yr, 98% stable) from spikes (HYPE's +29% is ~8.6% persistent). |
| `13_funding_carry_backtest.ipynb` | `data/defi.duckdb` | Net-of-costs backtest of the delta-neutral carry: gross funding − fees − spot-borrow. XMR nets ~37.6% (stable, no borrow); liquid majors ~4–5%; negative-funding alts depend on borrow. Flags that the reported Sharpe/DD are artifacts (funding smoothness, not basis/liquidation risk). |

All degrade gracefully on sparse data and re-run idempotently.

## Rendered dashboard (passive, no Jupyter)

`make render` (or `python scripts/render_notebooks.py`) executes every notebook **fresh
against current data** and writes dated HTML to `notebooks/rendered/<UTC date>/`,
with an `index.html` and a `latest` symlink — open `notebooks/rendered/latest/index.html`
in a browser. The rendered dashboard is **dark-themed** (chrome, matplotlib, and
plotly); the render job sets `NB_DARK=1`, which the notebooks honor — interactive
Jupyter stays light unless you export `NB_DARK` yourself. Each notebook is retried a few times (read-only DuckDB opens can
collide with a capture cron's brief write lock); old days prune to `KEEP` (14).
The rendered output is gitignored. A daily cron runs it at 04:30 UTC (after the
nightly backup), logging to `data/render.log`.
