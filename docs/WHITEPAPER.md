# Sportsball: A Reproducible, Honestly-Evaluated Harness for Sports-Market Modeling

**A technical white paper.** Version 0.4 · NBA moneyline.

---

## Abstract

Sportsball is a paper-trading research harness that takes a sports market from raw
price to a sized (simulated) position through an auditable pipeline: ingest →
model win probability → price expected value → size with fractional Kelly →
risk-check → settle. It is built around one principle that most "betting model"
projects quietly violate — **honesty about edge**. The engine refuses to trade on
a probability it did not model itself, every probability is evaluated
**out-of-sample**, and features that do not earn their keep are reported as such
rather than narrated into significance. This paper documents the architecture, the
quantitative methodology, and — most importantly — the measured results, including
the negative ones. On 49,108 real NBA games the win-probability model reaches a
holdout log-loss of **0.6308** (Brier **0.2201**) and, after temperature
calibration, a recent-season Brier of **0.195**. We show which features carry that
signal (Elo, schedule, point-in-time net-efficiency) and which do not (current- and
even point-in-time roster aggregates), and we are explicit that the system ships
**no proven monetary edge**.

---

## 1. Motivation

Public sports-betting "models" tend to share three failure modes: they evaluate
in-sample (and so overstate accuracy), they trade on un-modeled or random
probabilities, and they present every feature as a win. Sportsball is an attempt to
build the opposite — a system whose claims are reproducible and whose limitations
are documented in the same breath as its capabilities.

It runs in **paper / simulation mode** (`EXECUTION_MODE=PAPER`) and is intended as a
research and engineering reference, not a money printer. Where a component is
aspirational or unintegrated, the docs and this paper say so.

---

## 2. System architecture

The runtime is a **"cluster in a box"**: a single Python package and Docker image,
with each role exposed as a console entrypoint, decoupled through a Redis broker.

```
[Oracle] [Scout] ──RPUSH market_signals──▶ [Engine] ──┐
                                                        ├─(execution_signals)──▶ [Sniper] ─▶ paper fill
                       (optional approval gate) ◀───────┘                              │
                                                                                       ▼
                                                                  [Settlement] grades vs FINAL events
```

* **Producers** — the *Oracle* (odds feeds, mock mode without a key) and *Scout*
  (live Polymarket CLOB order books via the Gamma API) normalize prices into a
  single `market_signal` schema and `RPUSH` them onto `market_signals`.
* **Engine** — the brain. Models $P_{\text{true}}$, prices $EV$, sizes with Kelly,
  runs portfolio risk checks, and emits an execution signal (or, with the optional
  human-in-the-loop gate enabled, a Slack suggestion that only proceeds on Approve).
* **Sniper** — simulates a fill with slippage, records the trade, and tracks
  exposure in a Redis hash.
* **Settlement** — joins open trades to FINAL events on a foreign key, grades
  WIN/LOSS, writes realized PnL, and reaps exposure.

**Reliability.** Consumers use a reliable-queue pattern (`BRPOPLPUSH` into a
per-consumer in-flight list + explicit ack), so a crash mid-processing recovers the
message rather than dropping it.

**Persistence.** A normalized PostgreSQL schema (`events`/`signals`/`trades`) keyed
by a **canonical `event_id`** (`nba_YYYYMMDD_away_at_home`) lets every venue and feed
collapse the same game onto one row via foreign-key joins — replacing the original
fragile `LIKE '%'||id||'%'` substring matching. A separate, portable **DuckDB**
store holds the research dataset (below).

---

## 3. Data

All training data is **free and key-less**, pulled from `stats.nba.com` via
`nba_api`:

| Store | Contents | Scale |
|---|---|---|
| Postgres `events` | team-level game results (canonical id, scores) | 49,108 games, 43 seasons (1983–2026) |
| DuckDB `events` | same, portable/offline | 49,108 games |
| DuckDB `player_game_logs` | per-player box scores (incl. `plus_minus`) | **1,012,331 player-games**, 3,584 players (99.98% linked to an `event_id`) |

The DuckDB store is a parallel research dataset (not the live operational DB); the
team-level history is loaded into Postgres for the modeling loop via `make bootstrap`.

---

## 4. Quantitative methodology

`P_true` comes **only** from a trained model; with none loaded (or a stale-shaped
artifact) the Engine *abstains* rather than guess.

### 4.1 Ratings — margin-aware Elo with season carryover

Each team carries a rating $r$ (init 1500). Home expected score:

$$E_{\text{home}} = \frac{1}{1 + 10^{(r_{\text{away}} - (r_{\text{home}} + \text{HFA}))/400}}$$

Updates use a margin-of-victory multiplier (FiveThirtyEight's auto-correlation
correction) and regress toward 1500 across the off-season:

$$r' = r + K\,m\,(S - E),\quad m = \ln(|\Delta|+1)\cdot\frac{2.2}{0.001\,e_w + 2.2};\qquad
r \leftarrow 1500 + c(r-1500)\ \text{at a new season.}$$

$K$ and HFA are fit by L-BFGS-B minimizing Elo log-loss over the full history
(result: $K\approx 9.9$, $\text{HFA}\approx 84$).

### 4.2 Feature vector

A **shared, pure feature builder** produces a 7-vector — every element a difference
of per-team quantities, computed identically at train and serve:

`elo_diff_hfa`, `net_rating_diff` (point-in-time season-to-date margin),
`rest_diff`, `b2b_home`, `b2b_away`, `form_diff` (rolling win%),
`player_strength_diff` (point-in-time roster strength).

The two enrichment features are **point-in-time** — season-to-date using *prior
games only*, reset at season boundaries — not current-season constants. This
distinction turns out to matter (§5).

### 4.3 Probability, calibration, sizing

A `StandardScaler → LogisticRegression` pipeline maps the vector to $P_{\text{true}}$.
Because the raw logistic is over-confident out-of-sample, a single **temperature**
$T$ (fit on a held-out tail, $T\approx1.17$) rescales the logit:
$P_{\text{cal}} = \sigma(\text{logit}(P)/T)$.

Value and stake:

$$EV = P_{\text{true}}\cdot\text{odds} - 1,\qquad f = c\cdot\max\!\Big(0, \tfrac{EV}{\text{odds}-1}\Big)\ (c=0.25),$$

then a `PortfolioRiskManager` clamps $f$ against a global-exposure ceiling and a
same-event correlation penalty. A separate `ArbitrageEngine` flags risk-free locks
when $\sum 1/\text{odds}_i < 1$. Full derivations: [QUANT.md](QUANT.md).

### 4.4 Train/serve symmetry

The hardest correctness property. Training has full forward history; serving has
only the current game's `event_id` (date decodable) plus a persisted per-team
snapshot (Elo, last-game date, rolling form, season-to-date net-eff and roster, and
the season). Because both paths call the *same* `build_feature_row` and read HFA
from the persisted `model_meta.json`, the served feature vector cannot drift from
the trained one. A schema-version/width guard makes a stale artifact **abstain**
(prompting a retrain) instead of feeding a wrong-width vector.

---

## 5. Results

All metrics are a **chronological holdout** (fit on the earlier ~85% of games, score
the most recent ~15% — 7,366 test games), so they reflect generalization, not fit.

### 5.1 Feature ablation

| feature set | holdout log-loss | Δ vs Elo |
|---|---|---|
| Elo only | 0.6371 | — |
| + rest / b2b / form | 0.6317 | −0.0054 |
| + net-efficiency (point-in-time) | **0.6308** | **−0.0063** |
| + roster strength (point-in-time) | 0.6308 | −0.0063 |

**What works:** Elo plus the schedule features (rest, back-to-back, recent form)
carry most of the lift; point-in-time net-efficiency adds a further, real −0.0009.

**What doesn't (and we say so):** the *current-season* versions of net-rating and
roster strength added ~0 — collinear with Elo and stale for old games. The
*point-in-time* roster feature, despite a 1M-row precompute, also adds ~0 beyond
net-efficiency (season-to-date roster quality and team margin are collinear). It is
retained at weight ~0 with the machinery ready for a better roster metric, but it is
**not** a current source of edge.

### 5.2 Calibration

The raw logistic is systematically over-confident (predicted 0.65 → actual 0.58;
ECE ≈ 0.053). Temperature scaling cuts ECE **~26% (0.053 → 0.042)** out-of-sample and
improves Brier and log-loss — and, unlike isotonic regression (which overfit the
temporal shift), it is a single robust parameter. This matters directly: $EV$ and
Kelly trust the probability *level*, so over-confidence silently over-stakes.

### 5.3 Headline numbers

Holdout log-loss **0.6308**, Brier **0.2201**, accuracy **0.647**. On the calibrated
current-season slice, Brier **0.195** / log-loss **0.575** — comfortably under the
0.25 Brier "competitive" benchmark, though this slice is in-sample-optimistic and
should be read alongside the holdout. A hyperparameter sweep over
MOV/carryover/form-window moved log-loss within noise, so the defaults stand
(MOV-on being the one clearly justified Elo setting).

### 5.4 Betting backtest — skill vs. edge

A walk-forward betting simulation ([`scripts/backtest.py`](../scripts/backtest.py),
`make backtest-sim`) bets fractional-Kelly on the 14,733-game holdout. Because the
free data has no market odds, we bracket reality with a synthetic market and a
realistic 4.5% vig:

| market | vig | bets | win % | ROI | max DD |
|---|---|---|---|---|---|
| naive (Elo-only book) | 0% | 12,303 | 44.9% | +21.2% | 33% |
| naive (Elo-only book) | 4.5% | 8,129 | 40.7% | +23.6% | 36% |
| efficient (book = our model) | 0 / 4.5% | **0** | — | 0% | 0% |

The model has real **skill**: against a book that prices on Elo alone it extracts a
large, vig-surviving ROI by backing mispriced underdogs (sub-50% win rate, +EV). But
it shows **no edge against an efficient book** — priced at our own best estimate, it
finds *zero* +EV bets. A real sportsbook sits close to the efficient end, so this is
the honest takeaway: demonstrable modeling skill, **no demonstrated edge over a sharp
market**. (Note the 4.5%-vig naive row has higher ROI on fewer bets — vig filters to
the highest-edge plays — which is a turnover effect, not extra alpha.)

**Robustness** (`make backtest-sim ANALYZE=1`, naive book, 4.5% vig). The skill is
not an artifact of one era or threshold:

* **Every season 2013–2025 is profitable** (+9% to +33% ROI) — persistent, not a
  lucky window.
* **Selectivity monotonically raises ROI**: tightening the EV buffer
  0.00 → 0.20 lifts ROI 22.6% → 40.9% on far fewer bets — the model's
  highest-confidence disagreements are its best.
* **The edge lives in underdogs**: ROI by offered price is +43% on 3.0+ longshots
  (28% win) and ~0% on <1.5 favorites — i.e., the Elo-only book systematically
  *underprices underdogs* and the schedule/point-in-time features detect it.

This locates the skill precisely (differential information on underdogs) while
leaving the headline honest: it is skill versus a weak market, and a sharp book
prices underdogs correctly.

---

## 6. Engineering

* **Reproducibility.** 233 unit tests run with in-memory fakes — no Redis, Postgres,
  network, or `slack_sdk` on the default path — and on CI (GitHub Actions, Python
  3.11 + 3.12) on every push, alongside an offline end-to-end dry-run and an
  algorithm-lift measurement smoke (both on synthetic data).
* **Purity discipline.** `quant/` and the feature builder are I/O-free, which is what
  makes the math unit-testable and the train/serve contract enforceable.
* **Honest serving.** Model artifacts (`win_prob_model.pkl`, `team_state.json`,
  `model_meta.json`) carry a schema version; mismatch → abstain.
* **Observability & control.** An optional Slack integration emits fill/settlement/
  health alerts and a daily digest, plus a human-in-the-loop **approval gate** over
  Slack Socket Mode (no public endpoint): high-EV signals are *suggested* and only
  trade on Approve, with idempotent double-click handling and TTL auto-reject.
* **Live discovery.** The Scout resolves real Polymarket CLOB tokens and now derives
  canonical identity for head-to-head markets, so Polymarket NBA games are priceable
  by the model (previously inert).

---

## 7. Limitations (honest accounting)

1. **No proven monetary edge.** A competitive Brier is necessary, not sufficient;
   beating the *closing line* after vig is the real bar, and we do not claim it.
2. **CLV is dark on free data.** The free NBA ingest stores scores, not closing
   odds, so closing-line-value analysis needs a paid odds source.
3. **Enrichment is near-exhausted.** Elo already encodes most of what season-to-date
   team aggregates contain; further lift needs genuinely new signal (injuries/
   availability, lineup-level data, or market microstructure), not more aggregation.
4. **Cross-venue arbitrage is best-effort.** Polymarket does not expose home/away, so
   Oracle↔Polymarket `event_id` alignment relies on a convention.
5. **Live execution is intentionally unimplemented.** `EXECUTION_MODE` stays `PAPER`.
6. **Live integrations are smoke-tested, not in CI.**

---

## 8. Future work

Point-in-time **player availability / injuries** and **lineup-level** features (the
only plausible source of fresh signal); an order-independent matchup key to unlock
real cross-venue arb; a paid closing-odds feed to make CLV meaningful; and
multi-sport extension (the Elo/feature machinery is sport-agnostic; only ingest is
NBA-specific). The prioritized path — what's needed to *measure*, *have*, and *run*
a real edge — is in [ROADMAP.md](ROADMAP.md).

---

## 9. Conclusion

Sportsball demonstrates that a sports-market pipeline can be both capable and honest:
a real, calibrated, out-of-sample-validated NBA win-probability model inside a
reliable, testable, fully reproducible engineering harness — with its limits stated
as plainly as its results. The headline contribution is methodological as much as
quantitative: **measure out-of-sample, calibrate, and report the negative results**.

*See [ARCHITECTURE.md](ARCHITECTURE.md), [QUANT.md](QUANT.md), [SCHEMA.md](SCHEMA.md),
and [OPERATIONS.md](OPERATIONS.md) for component-level detail; all figures here are
reproducible via `make bootstrap → roster-pit → retrain → measure-features →
eval-duckdb`.*
