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
| `01_defi_explore.ipynb` | `data/defi.duckdb` | HL↔CEX basis over time, funding/OI cross-section, Polymarket crypto mids, CEX↔DEX lead-lag, and a first baseline predictive check (does basis predict the next CEX move). Fills in as the cron accumulates. |
| `02_model_eval.ipynb` | `data/sportsball.duckdb` | Offline walk-forward holdout of the v4 win-prob model — reliability/calibration curve, accuracy by confidence bucket, model-vs-market (CLV proxy). No Postgres needed. |

Both degrade gracefully on sparse data and re-run idempotently.
