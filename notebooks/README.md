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
| `05_ensemble_weight_sweep.ipynb` | `data/sportsball.duckdb` | Finds the best logistic↔GBT blend weight honestly (train/val/test 3-way split: pick weight on validation, report on test). Validation curve + unbiased test table. Result: w*≈1.0 (GBT-dominant) beats the current 50/50. |

Both degrade gracefully on sparse data and re-run idempotently.
