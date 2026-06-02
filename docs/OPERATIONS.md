# Operations Runbook

How to run, populate, monitor, and troubleshoot a Sportsball cluster. For the
*why* behind the design see [ARCHITECTURE.md](ARCHITECTURE.md); for the data
model see [SCHEMA.md](SCHEMA.md).

---

## 1. First run

```bash
cp .env.example .env          # then edit POSTGRES_PASSWORD (and RUNDOWN_API_KEY / SLACK_* if used)
docker compose up -d --build  # builds the single image, starts redis + postgres + the agents
docker compose logs -f        # watch the pipeline
```

If the Postgres volume predates the current schema (missing `events`/`signals`/
`trades`), apply it **non-destructively** — no volume wipe, no `sudo`:

```bash
make bootstrap                # idempotent init.sql + loads DuckDB history -> events
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

The Engine only trades on a model it trained — there is no edge out of the box,
and it's only as good as the data behind it.

```bash
# 1. Get DATA. Free, no API key — thousands of real NBA games from nba_api:
make ingest-nba                       # -> Postgres events (FINAL, scores). Default: last 4 seasons.
#    (Optional, needs RUNDOWN_API_KEY, adds closing lines for CLV:)
#    docker compose exec analytics_engine sportsball-backfill --managed
#    (Optional, offline research dataset -> DuckDB, not the model pipeline:)
#    python scripts/ingest_nba_duckdb.py           # 40+ seasons of team results
#    python scripts/ingest_player_stats_duckdb.py  # 1,012,331 player box scores (Moneyball)
#    (see SCHEMA.md "DuckDB analytics store")
# 2. (optional) point-in-time roster + availability features the model uses:
make roster-pit                       # DuckDB player logs -> team_strength_pit (season-to-date)
make ingest-injuries                  # DuckDB player logs -> team_availability_pit (who actually played)
#    (Real closing odds -> events.home/away_close, unblocks `make clv` + market_logit.
#     Free 2011-2022 path via the SBRO mirror; --duckdb writes the offline store directly:)
curl -sLO https://raw.githubusercontent.com/flancast90/sportsbookreview-scraper/main/data/nba_archive_10Y.json
sportsball-sbro-to-feed nba_archive_10Y.json -o odds.json   # mirror archive -> feed JSON
make ingest-odds FILE=odds.json       # -> Postgres events (or add --duckdb data/sportsball.duckdb)
# 3. Tune Elo hyperparameters by log-loss, then fit the model:
make retrain                          # = optimize + train (writes models/{model.pkl,team_state,meta})
```

Net-efficiency is computed point-in-time inside the Elo walk (no external fetch);
run `make roster-pit` and `make ingest-injuries` **before** `make retrain` to also
populate the point-in-time roster and availability features. Both are optional —
absent, those features are simply 0 (roster currently adds ~0; availability is the
open data-coverage lever, see [QUANT §2.5.1](QUANT.md)). `make ingest-odds` is
likewise optional but is what makes `make clv` and a real (vs synthetic-bracket)
backtest meaningful. `make retrain` writes
`models/{win_prob_model.pkl, team_state.json, model_meta.json}`; the Engine
hot-reloads and **abstains** if the artifact's schema doesn't match the code
(prompting another `make retrain`).

The **Retrainer agent** runs step 2 on a schedule (`RETRAIN_INTERVAL`, default
daily) and the Engine **hot-reloads** the new model automatically — no restart
needed. Run it manually with `make retrain` after a fresh `make ingest-nba`.

Validate before trusting it. The rigorous out-of-sample check is the walk-forward
holdout (no Postgres needed):

```bash
make eval-duckdb              # chronological holdout on the DuckDB store; Elo-only vs full
make dryrun                   # no data at all: synthetic season exercises the whole pipeline
```

`make dryrun` ([`scripts/offline_dryrun.py`](../scripts/offline_dryrun.py)) needs
no data, network, or DB — it generates a synthetic season and runs the real
`walk_forward` + holdout + betting backtest + closing-odds ingest, reporting the
availability feature's lift. Useful to confirm the full pipeline is healthy when
the live data sources are unreachable.

For the Postgres tools, persist the model's predictions then score them:

```bash
make backfill-signals         # model P_true per recent FINAL event -> signals
make evaluate                 # PRIMARY: CLV edge gate; SECONDARY: Brier/log-loss
make clv                      # CLV over all signals (primary) + filled trades
```

> **CLV is the primary edge gate** (positive Closing Line Value ≈ profitable
> long-run, and it converges in ~tens of bets vs thousands for P&L — see
> [RESEARCH_NOTES](RESEARCH_NOTES.md)). `make evaluate` now leads with CLV and
> treats Brier/log-loss as a secondary calibration check; `make clv` reports CLV
> over **all evaluated signals** (the largest sample) and over filled trades. Both
> need a closing-line source — `make ingest-odds` (or the Rundown backfill) —
> since the free NBA ingest stores scores only.
>
> `make evaluate`'s Brier here scores the **current season** with end-of-history
> ratings (the serving regime), so it reads optimistic (in-sample); treat
> `make eval-duckdb` as the honest generalization number, and `make backtest-viz`
> for the equity curve.

---

## 3. Monitoring

| Command | Shows |
|---------|-------|
| `make health` / `sportsball-health` | Redis + Postgres reachability, queue depth, exposure, row counts (exit code 0/1) |
| `make smoke` / `sportsball-smoke` | **Live** integration check: Gamma API markets, nba_api season, CLOB WebSocket (exit 0/1) |
| `make dashboard` | Terminal view: trades, realized PnL, arb count, favorite-hit baseline, latest executions |
| `make webui` (`[web]` extra) | **Web dashboard** at `http://127.0.0.1:8000` — KPIs (PnL/ROI/win%/CLV), equity curve, edge + model-status panels, recent signals/trades. Auto data source: Postgres → DuckDB → demo (`MODE=demo` to force; renders offline with no DB). |
| `make digest` / `sportsball-digest` | Posts a trailing-24h summary (PnL, exposure, counts, model age) to Slack; no-op without `SLACK_*` |
| `docker compose logs -f analytics_engine` | Per-signal `[SIGNAL]`/`[REJECT]`/`[ABSTAIN]`/`[ARBITRAGE]`/`[GATE]` decisions |

`make smoke` hits the real external services and reports what comes back — use it
to confirm the integrations work before relying on them:

```bash
make smoke                                   # all three checks
sportsball-smoke --skip-ws --nba-season 2023-24
```

Each check is isolated and the process exits non-zero on any failure. A "market
quiet" WARN on the WebSocket is normal (it connected; no book update arrived in
the window). Reference run: Gamma returned live markets, nba_api returned 1230
games for a full season, and the CLOB socket connected + accepted the subscribe.

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
| Scout connects but emits nothing | Gamma discovery returned no tradable tokens, or markets are quiet | Set `SCOUT_ASSET_IDS` to specific token ids, or raise `SCOUT_DISCOVERY_LIMIT` |
| Engine `[ABSTAIN]` even after training | Model trained on too little data | `make ingest-nba` (free NBA history) then `make retrain` |
| `make test` import errors | Package not installed in venv | `make setup` (or `pip install -e ".[tools]"`) |
| `permission denied` copying `data/sportsball_postgres` | The Postgres data dir is owned by the container's uid 70 (mode 0700) | Don't copy the dir — use `make backup` (in-container `pg_dump`); see §8 |
| `make backup` mirror skipped under cron | `MIRROR` points at a `gvfs` desktop mount cron can't see | Use an `/etc/fstab` CIFS mount (`/mnt/nas_1`); see §8 |

---

## 6. Configuration quick reference

Full tables are in the [README](../README.md#─── Configuration ───). The knobs
you'll touch most:

- `EXECUTION_MODE` — `PAPER` (default). Live execution is intentionally unimplemented.
- `config/settings.json` → `require_model` — keep `true` so the Engine never
  stakes on a probability it didn't model.
- `config/settings.json` → `kelly_multiplier`, `safety_buffer_ev`,
  `max_global_exposure_pct` — risk appetite.
- `SLACK_*` — optional Slack integration (all off by default); see §7.

---

## 7. Slack integration

All Slack features are **opt-in**. With no `SLACK_*` vars the notifier is a
no-op and the approval gate is disabled — the pipeline is unchanged. Design and
internals are in [ARCHITECTURE §6](ARCHITECTURE.md#6-notifications--the-approval-gate).

### 7.1 Alerts + digest (bot token *or* webhook)

The cheapest path is one-way alerts. Set **either** a bot token (scope
`chat:write`) **or** an incoming `SLACK_WEBHOOK_URL`, plus `SLACK_CHANNEL`:

```bash
SLACK_BOT_TOKEN=xoxb-...      # or: SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SLACK_CHANNEL=#sportsball
```

You then get a Slack card on every paper fill (Sniper), every settled WIN/LOSS
(Settlement), and on degraded `make health`. The scheduled summary is a separate,
run-on-demand command — wire it to cron:

```bash
make digest                            # host
docker compose run --rm digest         # container (for crontab)
```

### 7.2 The approval gate (Socket Mode)

To have high-EV signals *suggested* in Slack with Approve/Reject buttons — and
only traded on Approve — you need interactivity, which uses **Socket Mode** (no
public endpoint):

1. Create a Slack app → **OAuth & Permissions**: add bot scope `chat:write`,
   install to the workspace, copy the bot token (`xoxb-…`).
2. **Socket Mode**: enable it; create an app-level token with `connections:write`
   (`xapp-…`). Under **Interactivity**, Socket Mode delivers `block_actions`.
3. Configure and restart:

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_REQUIRE_APPROVAL=true
SLACK_APPROVAL_EV_THRESHOLD=0.10       # only gate signals at/above this EV
SLACK_APPROVAL_TTL_SECS=900            # auto-reject (EXPIRED) if unactioned
docker compose up -d --build approver  # the gatekeeper agent
```

With the gate on, the Engine logs `[GATE] … held for approval` and the trade
reaches the Sniper only after you click **Approve**. **Reject** discards it; no
click within the TTL auto-rejects (a missed decision never trades).

> The `approver` container starts regardless but **exits immediately** unless
> both Socket Mode tokens are present — that's expected when the gate is off.

---

## 8. Backups & off-site mirror

`make backup` snapshots the **durable** state into `backups/<UTC-timestamp>/`:

- **Postgres** (`events`/`signals`/`trades`) — a logical `pg_dump` run *inside*
  the `postgres` container. You **cannot** back this up by copying
  `data/sportsball_postgres`: it's owned by the container's uid 70 / mode 0700,
  so a host-side `cp -r` fails with *permission denied*. The dump sidesteps that.
- **DuckDB** research store (`data/sportsball.duckdb`) — file copy.
- **Model artifacts** (`models/`) + `optimized_params.json` — regenerable via
  `make retrain`, but cheap to snapshot.

Redis is intentionally skipped (broker/queue, rebuildable). The Postgres
container must be running for the DB dump; if it's down the script warns and
backs up DuckDB + models only.

```bash
make backup                                   # -> backups/<timestamp>/
MIRROR=/mnt/nas_1/sportsball make backup      # also mirror off-site
make backup DIR=/mnt/external                 # write the primary elsewhere
make restore DIR=backups/<timestamp>          # inverse (prompts; DESTRUCTIVE)
```

Retention is the newest `KEEP` snapshots per location (default 14), pruned after
each run. Tunables (env): `KEEP`, `MIRROR`, `PG_SERVICE`, `BACKUP_ROOT`.

**Off-site mirror.** Set `MIRROR` to a mounted path and each snapshot is copied
there (atomic `.partial`→rename) and pruned to `KEEP`; a missing/unwritable
`MIRROR` warns but never fails the local backup.

> **SMB/NAS caveat:** a desktop file-manager SMB mount lives under
> `/run/user/<uid>/gvfs/…` and is **invisible to cron** (no session bus). For an
> automated mirror, use a real CIFS mount (`/etc/fstab`, `nofail,x-systemd.automount`)
> at e.g. `/mnt/nas_1` and point `MIRROR` there. SMB can't store Unix
> perms/ownership, so the script copies with `cp -r` (not `-a`) by design.

**Scheduling.** Nightly host cron (runs even when nothing else is):

```cron
30 3 * * * cd /path/to/sportsball && PATH=/usr/local/bin:/usr/bin:/bin \
  MIRROR=/mnt/nas_1/sportsball ./scripts/backup.sh >> /path/to/sportsball/data/backup.log 2>&1
```

### 8.1 One-time NAS (CIFS) mount setup

Make a persistent, cron-visible mount of an SMB share (example:
`//drivemaster.local/nas_1` → `/mnt/nas_1`). Run once, as root:

```bash
sudo apt install -y cifs-utils

# Credentials in a root-only file (keeps the password out of fstab/scripts):
sudo tee /etc/cifs-sportsball >/dev/null <<'EOF'
username=YOUR_NAS_USER
password=YOUR_NAS_PASSWORD
EOF
sudo chmod 600 /etc/cifs-sportsball

sudo mkdir -p /mnt/nas_1
echo '//drivemaster.local/nas_1  /mnt/nas_1  cifs  credentials=/etc/cifs-sportsball,uid=1000,gid=1000,nofail,x-systemd.automount,_netdev  0  0' \
  | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /mnt/nas_1
```

Verify, then point a backup at it:

```bash
mount | grep /mnt/nas_1                            # type cifs, rw
MIRROR=/mnt/nas_1/sportsball ./scripts/backup.sh   # expect "mirrored OK"
```

- Set `uid`/`gid` to the user that runs the cron (`id -u` / `id -g`) so it can
  write. `nofail` + `x-systemd.automount` keep a missing NAS from hanging boot.
- Mount errors: `mount error(13)` = bad credentials; timeout/`(115)` = NAS
  unreachable; add `,vers=3.0` (or `,vers=2.1` for old firmware) on negotiation
  failures.

---

## 9. Odds: backfill, ongoing capture, CLV

Real closing odds are loaded for **2011-2026** and the served model uses them.

**Deep history (one-time):**

```bash
# Free moneyline, 2011-2022 (SBRO mirror):
curl -sLO https://raw.githubusercontent.com/flancast90/sportsbookreview-scraper/main/data/nba_archive_10Y.json
sportsball-sbro-to-feed nba_archive_10Y.json -o data/odds_feed.json
make ingest-odds FILE=data/odds_feed.json          # -> events (Postgres)

# Recent closing lines, 2022-present (The Odds API historical, ~10 cr/game-day):
make backfill-odds-history SINCE=2022-07-01         # ET-localized; --budget caps spend
make retrain                                        # activate market_logit on the new coverage
```

> **Order matters:** `bootstrap` resets `events.home/away_close` to NULL, so run
> it *before* the odds ingest, never after. Sequence: `bootstrap → ingest-odds →
> backfill-odds-history → ingest-injuries → retrain`.

**Ongoing capture (free):** `make capture-odds` hits the LIVE endpoint (~1
credit/call) and applies today's near-closing lines; scheduled via cron
(`15 17` + `0 20` local, near NBA tips). At ~2 credits/day it fits the free
500/mo tier, so deep history is the only thing the paid plan was needed for.

**Edge gate:** `make clv` reports Closing Line Value — the primary KPI. As of the
first real measurement the v4 model is **SUB-PAR (avg CLV −1.67%)**: a good
predictor that does not beat the sharp close. Gate the model on CLV before
trusting any backtest ROI.

**Research odds (paid, one-time):** `scripts/backfill_odds_markets_duckdb.py`
pulled **per-book h2h + totals** into DuckDB `odds_quotes` (248k quotes, 23 books)
— line-shopping + totals raw material; see SCHEMA.md.

---

## 10. Autonomy & boot persistence

The data pipeline runs **unattended and survives reboot / power loss** — no login
or manual restart needed. Verified pieces:

| Component | What runs it | Boot-safe |
|---|---|---|
| Intraday line capture | host **cron** (`capture_snapshot.py`, MLB every 2h, → DuckDB `odds_snapshots`) | `systemctl is-enabled cron` = enabled |
| Nightly backup → NAS | host **cron** (`backup.sh` 03:30, MIRROR=/mnt/nas_1) | same |
| Postgres / Redis / Engine | **Docker** restart policy (`unless-stopped` / `always`) | `systemctl is-enabled docker` = enabled |
| NAS mirror mount | `/etc/fstab` (`nofail`, `x-systemd.automount`) | mounts on boot/first access |

Robustness notes:
- **Capture needs no Postgres/Docker** — `capture_snapshot.py` writes only the
  DuckDB file, so it works the moment the host has network + disk, regardless of
  container startup timing.
- **Backup degrades gracefully** — its `pg_dump` needs the Postgres container; if
  it ever fired before Docker finished booting it just warns and still snapshots
  DuckDB + models. (03:30 is well after any boot, so moot.)
- User crontabs run via the cron daemon **without an interactive login**, so the
  machine can sit headless for weeks accumulating `odds_snapshots` for the
  lead-lag analysis.

Check the schedule with `crontab -l`; capture/backup logs land in
`data/odds_capture.log` and `data/backup.log`.
