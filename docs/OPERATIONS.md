# Operations Runbook

How to run, populate, monitor, and troubleshoot a Sportsball cluster. For the
*why* behind the design see [ARCHITECTURE.md](ARCHITECTURE.md); for the data
model see [SCHEMA.md](SCHEMA.md).

---

## 1. First run

```bash
cp .env.example .env          # then edit POSTGRES_PASSWORD (and RUNDOWN_API_KEY if live)
docker compose up -d --build  # builds the single image, starts redis + postgres + 5 agents
docker compose logs -f        # watch the pipeline
```

With no `RUNDOWN_API_KEY`, the Oracle runs in **mock mode** and the Engine
**abstains** unless a trained model is present in `models/` (one is shipped). To
see the full loop on fabricated data without any external dependency:

```bash
make setup                    # host venv + editable package install
make demo                     # seed ~500 FINAL events, signals, settled trades
make dashboard                # live performance view
make clv ; make evaluate      # closing-line value + Brier/log-loss
make plot                     # equity curve from realized PnL -> data/plots/
```

---

## 2. Producing a real edge (the modeling loop)

The Engine only trades on a model it trained — there is no edge out of the box.

```bash
# 1. Backfill finished games + closing lines (needs RUNDOWN_API_KEY)
docker compose exec analytics_engine sportsball-backfill --managed
# 2. Tune Elo hyperparameters by log-loss
make optimize        # writes optimized_params.json
# 3. Fit the win-probability model the Engine loads
make train           # writes models/win_prob_model.pkl + current_ratings.json
# 4. (optional) refresh NBA efficiency features
make fetch-stats
# 5. Restart the Engine so it loads the new model
docker compose restart analytics_engine
```

Validate before trusting it: `make backtest-viz` (walk-forward equity curve) and
`make evaluate` (Brier < 0.25 is competitive). `make clv` after live paper
trading is the truest edge signal.

---

## 3. Monitoring

| Command | Shows |
|---------|-------|
| `make health` / `sportsball-health` | Redis + Postgres reachability, queue depth, exposure, row counts (exit code 0/1) |
| `make dashboard` | Trades, realized PnL, arb count, favorite-hit baseline, latest executions |
| `docker compose logs -f analytics_engine` | Per-signal `[SIGNAL]`/`[REJECT]`/`[ABSTAIN]`/`[ARBITRAGE]` decisions |

---

## 4. Resetting the database

Postgres runs `config/init.sql` **only on an empty data directory**. After a
schema change (e.g. upgrading to the v0.2 normalized schema) the old volume will
not auto-migrate, so wipe and recreate it:

```bash
docker compose down -v        # WARNING: deletes data/sportsball_postgres + data/redis
docker compose up -d --build
make demo                     # reseed if you want demo data
```

For a production deployment you would instead apply a hand-written migration
rather than dropping the volume.

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Engine logs `[ABSTAIN] … no modeled probability` for everything | No trained model in `models/`, or signals lack a `matchup` | Run the modeling loop (§2); ensure the Oracle emits `metadata.matchup` |
| `relation "events" does not exist` | Old volume with the pre-v0.2 schema | Reset the DB (§4) |
| `psycopg2 … password authentication failed` | `.env` password ≠ the one baked into the existing volume | Reset the DB (§4) or align `POSTGRES_PASSWORD` |
| Dashboard / CLV say "no data" | No settled trades yet | `make demo`, or let the pipeline + settlement run |
| Scout connects but emits nothing | Placeholder `assets_ids` (Phase 3) | Set `SCOUT_ASSET_IDS` to real Polymarket asset ids |
| `make test` import errors | Package not installed in venv | `make setup` (or `pip install -e ".[tools]"`) |

---

## 6. Configuration quick reference

Full tables are in the [README](../README.md#─── Configuration ───). The knobs
you'll touch most:

- `EXECUTION_MODE` — `PAPER` (default). Live execution is intentionally unimplemented.
- `config/settings.json` → `require_model` — keep `true` so the Engine never
  stakes on a probability it didn't model.
- `config/settings.json` → `kelly_multiplier`, `safety_buffer_ev`,
  `max_global_exposure_pct` — risk appetite.
