# Quantitative Handbook: Project Sportsball

A deep dive into the mathematics behind Sportsball's trading logic, mapped to the
actual implementation in [`src/sportsball/quant/`](../src/sportsball/quant/) and
the modeling pipelines in [`src/sportsball/pipelines/`](../src/sportsball/pipelines/).

> **Honesty note.** Two things are *live* on the Engine's per-signal path: the
> **Elo → logistic** win-probability model and the **EV → Kelly → portfolio**
> valuation chain. The **Poisson** and **Monte Carlo** routines (§5) are pricing
> primitives that exist and are unit-tested but are **not** wired into the
> single-market decision path today. Where a formula is aspirational or
> unintegrated, it says so.

---

## 1. The algorithm, end to end

```text
        OFFLINE (pipelines, on a schedule)              LIVE (Engine, per signal)
  ┌────────────────────────────────────────┐   ┌──────────────────────────────────┐
  events (FINAL scores)                          market_signal {odds, matchup, …}
        │                                              │
        ▼  walk Elo forward (MOV + carryover)          ▼  ModelBundle.predict
  9-feature row + outcome per game                build_feature_row(...) [shared]
        │                                              │
        ▼  optimize K, HFA  (L-BFGS-B, log-loss)       ▼  scaler+logistic σ(·) → P_true
  optimized_params.json                                │
        │                                              ▼  EV = P_true·odds − 1
        ▼  fit Pipeline(scaler, logistic)              │     (gate: EV > buffer)
  win_prob_model.pkl + team_state.json + meta          ▼  f* = EV/(odds−1); f = c·f*
        └──────────────► loaded as ModelBundle ◄───────┤
                                                        ▼  PortfolioRiskManager
                                                   (exposure ceiling + corr. penalty)
                                                        ▼  execution_signal
```

The offline half (`sportsball-optimize`, `sportsball-train`, re-run by the
Retrainer) produces the model artifacts; the live half loads them and prices
each incoming market. The two halves communicate only through the files in
`models/` — the Engine hot-reloads them when the mtime changes.

---

## 2. The win-probability model (live)

`P_true` comes **only** from a trained [`ModelBundle`](../src/sportsball/quant/models.py).
With no (or a stale-shaped) model loaded the Engine **abstains**
(`strategy.require_model`) — it never falls back to a producer-supplied or random
probability. The model has three pieces: an Elo rating system, a **shared
feature builder**, and a standardizing logistic. The feature builder
([`quant/features.py`](../src/sportsball/quant/features.py)) is the single source
of truth called by *both* training and serving — that's what guarantees the live
path can't drift from what was trained.

### 2.1 Elo rating system (with MOV + carryover)

Each team carries a rating $r$ (initialized to **1500**). The home team's
expected score, given a home-field advantage $\text{HFA}$ in Elo points
([`_elo.py`](../src/sportsball/pipelines/_elo.py)):

$$E_{\text{home}} = \frac{1}{1 + 10^{\,(r_{\text{away}} - (r_{\text{home}} + \text{HFA}))/400}}$$

After each result, ratings update by the zero-sum Elo rule, scaled by a
**margin-of-victory** multiplier:

$$r_{\text{home}}' = r_{\text{home}} + K\cdot m\cdot(S - E_{\text{home}}), \qquad m = \ln(|\Delta| + 1)\cdot\frac{2.2}{0.001\,e_w + 2.2}$$

where $S \in \{1, 0.5, 0\}$ is the outcome, $\Delta$ the score margin, and $e_w$
the winner's pre-game Elo edge. Larger blowouts move ratings more; the $e_w$
denominator damps the favorite's auto-correlation (FiveThirtyEight's correction).
Between seasons — detected as a per-team gap exceeding `elo_offseason_gap_days`
(90) — ratings **mean-revert** toward 1500: $r \leftarrow 1500 + c_{\text{carry}}(r - 1500)$
with `elo_carry` = 0.75. `walk_forward` replays history in date order, emitting the
full feature row (§2.3) and outcome per game and returning each team's final state.

### 2.2 Hyperparameter optimization

$K$ and $\text{HFA}$ are not hand-picked; they are fit to history by minimizing
**mean log-loss** over the Elo expected-scores
([`optimize.py`](../src/sportsball/pipelines/optimize.py)):

$$\mathcal{L}(K, \text{HFA}) = -\frac{1}{N}\sum_{i=1}^{N}\Big[S_i \ln p_i + (1 - S_i)\ln(1 - p_i)\Big], \quad p_i = \text{clip}(E_{\text{home},i},\, 0.001,\, 0.999)$$

minimized with **L-BFGS-B** (`scipy.optimize.minimize`) over bounds
$K \in [5, 100]$, $\text{HFA} \in [0, 200]$, starting from $(20, 50)$. The result
is written to `optimized_params.json` and consumed by the trainer.

### 2.3 The feature vector

Rather than a single Elo differential, the model consumes a **9-feature** vector
(`FEATURE_ORDER` in `features.py`), every element antisymmetric under a home/away
swap so any missing input degrades to a neutral 0:

| # | Feature | Meaning |
|---|---------|---------|
| 1 | `elo_diff_hfa` | $(r_{\text{home}} + \text{HFA}) - r_{\text{away}}$ |
| 2 | `net_rating_diff` | home − away **point-in-time** net efficiency (season-to-date avg margin) |
| 3 | `rest_diff` | days since each team's last game (capped at 10) |
| 4 | `b2b_home` | 1 if home is on a back-to-back |
| 5 | `b2b_away` | 1 if away is on a back-to-back |
| 6 | `form_diff` | home − away rolling win% over the last `form_window` (10) games |
| 7 | `player_strength_diff` | home − away **point-in-time** roster strength (§2.5) |
| 8 | `availability_diff` | home − away **point-in-time** roster availability (§2.5.1) |
| 9 | `market_logit` | logit of the **no-vig market probability** the home side wins (§2.5.2) |

The first two enrichment features are **point-in-time** (season-to-date, prior games only) —
not the current-season constants the first version used. `net_rating_diff` is
computed inside the Elo walk from game scores (no external fetch); `player_strength_diff`
comes from the precomputed `team_strength_pit` table. Both reset to 0 at the start of
a new season (no prior games) — handled identically at train and serve. Net-rating is a
**learned feature** the logistic weights, not a hardcoded `×20` nudge into the Elo diff.

### 2.4 Standardizing logistic

The trainer ([`train.py`](../src/sportsball/pipelines/train.py)) fits a
`Pipeline(StandardScaler, LogisticRegression)` on the feature matrix — the scaler
puts the disparate scales (Elo ~hundreds, form ~0–1, rest ~0–10) on equal footing
and is persisted so serving standardizes with the same statistics:

$$P_{\text{true}} = \sigma\!\Big(\beta_0 + \sum_j \beta_j\, z_j\Big),\qquad z_j = \frac{x_j - \mu_j}{\sigma_j}$$

For the side named by the market, `predict_participant_prob` returns $P_{\text{true}}$
for the home side or $1 - P_{\text{true}}$ for the away side.

**Ensemble (default).** Serving is by default a **GBT-dominant** blend of this
logistic and a gradient-boosted tree (`HistGradientBoostingClassifier`) over the
same features — "ensemble many decorrelated signals"
([RESEARCH_NOTES](RESEARCH_NOTES.md)): the tree captures interactions, the linear
model adds diversification, and they err differently. The GBT share is
`strategy.ensemble_gbt_weight` (**default 0.75**, logistic gets the rest). This was
**validated** by a 3-way train/val/test sweep (`notebooks/05_ensemble_weight_sweep`):
the validation optimum was the boundary (GBT-only), and out-of-sample on the test
set GBT-dominant beats the old 50/50 on **accuracy (0.678 vs 0.666), log-loss, AND
calibration** (raw-blend ECE drops monotonically with GBT weight — the tree is the
better-calibrated learner here, contrary to the usual prior). Kept at 0.75 rather
than 1.0 for a little diversification. `quant/models.EnsembleModel` averages their
weighted `predict_proba`, exposes the sklearn API, and pickles transparently into
the bundle — same 9-feature contract, **no schema change**. Toggle the whole
ensemble with `strategy.model_ensemble`; the GBM is best-effort (a failure falls
back to the logistic alone). The calibrator is fit on the *same* construction used
to serve, so calibration matches the ensemble.

### 2.5 Player-derived roster strength (Moneyball)

The `player_strength_diff` feature is a **point-in-time** roster strength from the
DuckDB player logs ([`scripts/precompute_roster_pit.py`](../scripts/precompute_roster_pit.py),
`make roster-pit`): for each team-game, the top-8 players by minutes (over the
team's *prior* games that season) and their minutes-weighted mean per-minute
plus-minus, written to `team_strength_pit` (one row per team-game) and joined
leakage-free at train time; serving uses the team's latest season-to-date value
from the snapshot. (The older `make player-strength` writes the current-season
constant — superseded by the point-in-time version.)

**Honest result:** the ablation (§2.7) shows point-in-time roster adds ~0 *beyond*
point-in-time net-efficiency — season-to-date roster quality and team margin are
collinear. It's kept (harmless, weight ~0) with the machinery ready for a better
roster metric (RAPM, availability), but it is not currently a source of edge.

### 2.5.1 Point-in-time availability (the injuries lever)

`availability_diff` (feature 8, added in schema v3) is the roadmap's highest-value missing
signal — *who is actually playing tonight*, the reason season roster strength was
flat. It is defined as **the season-to-date strength of the players actually
available**, computed identically at train and serve so the two can't drift:

* **Train (leakage-free, from the player logs,
  [`ingest_injuries.py`](../src/sportsball/pipelines/ingest_injuries.py),
  `make ingest-injuries`):** the players who logged minutes in a game *are* its
  available roster; each is scored from their **prior** games that season only
  (never the current game), so a rested or absent star simply isn't in the set and
  the number drops. Written one row per team-game to `team_availability_pit`.
* **Serve:** the same scalar over the full roster minus the players ruled out on
  tonight's injury report; the Engine reads the latest value per team
  (`store.team_availability`) and passes it to the model.

With no availability rows the feature is **inert** (neutral 0) and the model is
identical to v2 — so it is honest-by-default, activating only once availability
data is loaded and a retrain runs. That it is *not* inert when data carries signal
(adds holdout lift, learns a positive coefficient, shifts the served probability)
is proven on a synthetic season by
[`tests/test_availability_integration.py`](../tests/test_availability_integration.py)
and demonstrated end-to-end by `make dryrun`
([`scripts/offline_dryrun.py`](../scripts/offline_dryrun.py)). What remains for
real edge here is **data coverage/quality**, not plumbing.

### 2.5.2 The market line as a feature (Benter)

`market_logit` (feature 9, schema v4) feeds the **market's own probability into the
model** — the single highest-evidence upgrade from the research ([RESEARCH_NOTES](RESEARCH_NOTES.md)):
Bill Benter's most profitable move was adding the public's implied probability as
an input to his handicapping model. We use the **no-vig** estimate (de-vig the two
decimal prices so they sum to 1, `odds.devig_two_way`), take its logit, and let the
logistic weight it alongside Elo/rest/form.

* **Train:** de-vig `events.home_close/away_close` (`make ingest-odds`) into
  P(home) per game, joined leakage-free in the Elo walk.
* **Serve:** the Engine de-vigs the best two-sided price standing in the arbitrage
  book for the matchup; unknown → neutral 0 (= logit 0.5), so it never biases a side.

**Caveat (the "echo the market" risk):** if the model is trained on, and bet
against, the *same* line, it just learns to reproduce the market and finds ~no edge
(which is honest, not a bug). The value is training on the **closing** line while
betting an earlier/better price (capturing the move), and benchmarking edge as CLV
vs. a sharp close. See [ROADMAP Tier 2.4](ROADMAP.md) and issue #1.

**Now loaded and measured (not inert).** Real closing odds (the SBRO mirror,
2011-2022, 12,505 games) are ingested into the DuckDB `events`, and
`scripts/train_eval_duckdb.py` builds the feature and reports its out-of-sample
lift: on lined holdout games log-loss 0.6506 → 0.6462 (+0.0044) and accuracy 0.6245
→ 0.6361; blended over all test games (only ~32% carry a line) it is +0.0020. The
feature still falls back to neutral 0 wherever a line is absent, so the "inert when
missing" contract holds. The *served* model activates `market_logit` once a Postgres
retrain runs (`make ingest-odds` → `make retrain`); shipped artifacts are still v2.

> **Train/serve symmetry (and the HFA fix).** The same `build_feature_row` runs in
> both paths, and `hfa` is read from the persisted `model_meta.json` rather than a
> hardcoded constant — closing the old skew where serving used `DEFAULT_HFA=50`
> regardless of what training optimized. Training has full forward history; serving
> has only `team_state.json` (per-team elo, last-game date, form) + the date decoded
> from the `event_id`. A schema-version/width guard in `ModelBundle.load` makes a
> stale-shaped artifact **abstain** (prompting `make retrain`) rather than feed the
> model a wrong-width vector.

### 2.6 Calibration (auto-selected: temperature or isotonic)

The raw logistic is **systematically over-confident out-of-sample** — on a
chronological holdout, predicted 0.65 came back 0.58, predicted 0.75 → 0.68
(Expected Calibration Error ≈ 0.053). Since `EV = P_true·odds − 1` and Kelly both
trust the probability *level*, over-confidence quietly over-stakes. The trainer
fits a calibrator on a held-out recent tail and persists a small JSON spec in
`model_meta.json` (`calibration`); the bundle applies it purely (numpy) to every
prediction. Two calibrators are considered ([`quant/calibration.py`](../src/sportsball/quant/calibration.py)):

* **temperature** — divide the logit by a scalar `T`:
  $P_{\text{cal}} = \sigma\!\big(\tfrac{1}{T}\,\operatorname{logit}(P)\big)$, `T>1`
  shrinks toward 0.5 (monotonic — ranking/AUC unchanged);
* **isotonic** — a monotonic piecewise-linear remap (applied via `np.interp` on the
  fitted knots), which can fix non-uniform miscalibration temperature can't.

`method="auto"` fits both on one half of the tail, scores log-loss on the other,
and keeps the winner (else `identity`), so isotonic is chosen only when it
*generalizes*. On a well-calibrated model `auto` correctly picks `identity`; on a
deliberately over-fit one (`make measure-algos`, regime B) temperature cut holdout
log-loss ~1.03 → 0.42. The **Engine then shrinks the Kelly stake by the model's
calibration-confidence** (`calibration.confidence`, `strategy.uncertainty_scaling`)
— a more-tempered model stakes less. Older artifacts fall back to the legacy scalar
`temperature` (default `T=1.0`, no-op).

### 2.7 What the features actually contribute

A holdout ablation ([`scripts/measure_features.py`](../scripts/measure_features.py),
[`model_quality.py`](../scripts/model_quality.py)) is honest about feature value:

| feature set | holdout log-loss | Δ vs Elo-only |
|---|---|---|
| Elo only | 0.6371 | — |
| + rest / b2b / form | 0.6317 | −0.0054 |
| + net-eff (point-in-time) | 0.6308 | **−0.0063** |
| + roster (point-in-time) | 0.6308 | −0.0063 |

The schedule features (rest, back-to-back, form) carry most of the lift beyond Elo.
**Point-in-time net-efficiency adds a further −0.0009** — small but real, and where
the *current-season* version added ~0 (it was collinear with Elo and stale for old
games). Point-in-time **roster** strength adds nothing more (collinear with net-eff);
it's retained at weight ~0 pending a better roster metric. A sweep over
MOV/carryover/`form_window` moved log-loss within noise, so the defaults stand —
MOV-on is the one Elo setting that clearly helps.

---

## 3. Valuation and sizing (live)

### 3.1 Expected value

The market-implied probability of a decimal price is $P_{\text{market}} = 1/\text{Odds}$.
A signal is valued by its per-unit expected value
([`odds.py`](../src/sportsball/quant/odds.py)):

$$EV = (P_{\text{true}} \times \text{Odds}) - 1$$

The Engine emits an execution signal only when $EV > \text{safety\_buffer\_ev}$
(default `0.02`) — a cushion against model variance, not a free parameter to chase.

### 3.2 Fractional Kelly

Stake is sized for logarithmic-growth optimality, then shrunk to control
estimation error:

$$f^{*} = \frac{EV}{\text{Odds} - 1}, \qquad f_{\text{actual}} = c \cdot \max(0,\, f^{*})$$

with Kelly multiplier $c = \text{kelly\_multiplier}$ (default **0.25**,
quarter-Kelly). The `max(0, ·)` means a non-positive edge never produces a
position, and odds $\le 1$ (no payout) stake nothing.

### 3.3 Portfolio risk coordination

Before the stake is committed, [`PortfolioRiskManager`](../src/sportsball/quant/portfolio.py)
clamps it against two constraints, reading current exposure from the Redis
`active_trades` hash:

1. **Global exposure ceiling.** With aggregate open exposure $E$ and ceiling
   $E_{\max} = \text{max\_global\_exposure\_pct}$ (default `0.15`), a stake is
   *downsized* to the remaining headroom $\max(0,\, E_{\max} - E)$, and rejected
   outright if there is none.
2. **Correlation penalty.** If an open position already references the same
   `event_id`, the stake is multiplied by
   $\text{correlation\_penalty\_multiplier}$ (default `0.5`) — you don't want two
   fully-sized, correlated bets on one game.

The surviving size (possibly 0) is what the Sniper actually fills.

---

## 4. Cross-market arbitrage

Independently of statistical edge, [`ArbitrageEngine`](../src/sportsball/quant/arbitrage.py)
maintains a per-event book of the **best (highest) odds seen per side** across
venues. A risk-free "lock" exists when the implied probabilities of the mutually
exclusive outcomes sum below 1:

$$S = \sum_{i=1}^{n}\frac{1}{\text{Odds}_i} < 1, \qquad M = (1 - S)$$

where $M$ is the guaranteed profit margin. When $S < 1$ (and both sides are
present), the Engine emits an `ARBITRAGE`-typed signal with one leg per outcome,
allocating capital so the return is identical regardless of result:

$$\text{Allocation}_i = \frac{1/\text{Odds}_i}{S}$$

*Example:* with $S = 0.95$, a budget returns $1/0.95 \approx 1.0526\times$ — a
5.26% locked profit. (Cross-venue arb is gated by both venues producing the same
canonical `event_id`; see [ARCHITECTURE §5](ARCHITECTURE.md#5-known-limitations).)

---

## 5. Derivative-pricing primitives (not yet on the live path)

These price *derivative* markets (spreads, totals) where the moneyline logistic
isn't enough. They are implemented and unit-tested but **not** invoked by the
Engine's current single-market loop — wiring them in is future work.

### 5.1 Poisson scoring

For discrete-scoring contexts, model scoring events $k$ at mean rate $\lambda$
([`poisson.py`](../src/sportsball/quant/poisson.py)):

$$P(X = k) = \frac{\lambda^{k} e^{-\lambda}}{k!}$$

`joint_poisson_matrix(λ_a, λ_b)` builds the outer product
$M_{ij} = P(A=i)\,P(B=j)$ over $0 \le i,j < 50$; summing the cells where
$i - j > \text{spread}$ or $i + j > \text{total}$ prices the spread/total line.

### 5.2 Monte Carlo

[`MonteCarloPricer`](../src/sportsball/quant/models.py) simulates $N$ (default
10,000) games by drawing each team's score from a **Gaussian**
$\mathcal{N}(\mu, \sigma)$ and counting outcomes:

$$P_{\text{ML}} = \tfrac{1}{N}\#\{A > B\}, \quad P_{\text{spread}} = \tfrac{1}{N}\#\{A - s > B\}, \quad P_{\text{over}} = \tfrac{1}{N}\#\{A + B > T\}$$

The Gaussian is a deliberate simplification; a fuller version would draw from
pace/efficiency-derived distributions, which is why this stays a primitive rather
than a live pricer for now.

---

## 6. Parameter reference

| Symbol | Code | Default | Where |
|--------|------|---------|-------|
| $K$ | `k_factor` | optimized (start 20) | Elo update step |
| $\text{HFA}$ | `hfa` | optimized (start 50) | Elo home edge — persisted in `model_meta.json`, used identically train & serve |
| $m$ | `elo_mov_enabled` | True | margin-of-victory multiplier |
| $c_{\text{carry}}$ | `elo_carry` | 0.75 | season carryover toward 1500 |
| gap | `elo_offseason_gap_days` | 90 | gap that triggers carryover |
| $N$ | `form_window` | 10 | rolling-form window |
| $T$ | `temperature` | fit (~1.17) | post-hoc calibration (persisted in `model_meta.json`) |
| buffer | `safety_buffer_ev` | 0.02 | EV gate |
| $c$ | `kelly_multiplier` | 0.25 | Kelly shrink |
| $E_{\max}$ | `max_global_exposure_pct` | 0.15 | exposure ceiling |
| — | `correlation_penalty_multiplier` | 0.5 | same-event penalty |

Risk + Elo knobs live in `config/settings.json` ([StrategyConfig](../src/sportsball/config.py));
$K$/HFA are optimized into `optimized_params.json` (`make optimize`). The trained
artifacts are `models/{win_prob_model.pkl, team_state.json, model_meta.json}`
(`make train`). `model_meta.json` carries the feature contract + hfa; a version/width
mismatch makes the Engine abstain until you `make retrain`.
