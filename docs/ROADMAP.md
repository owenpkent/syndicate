# Roadmap: What Sportsball Needs

A prioritized, honest accounting of what the system needs — to *measure* a real
edge, to *have* one, and to *run* live. Grounded in what we've actually measured:
the model is well-calibrated and out-of-sample skillful (holdout Brier 0.220), it
beats a naive Elo-only book by a wide, season-robust margin, but against an
**efficient** book it finds **zero +EV bets** ([WHITEPAPER §5.4](WHITEPAPER.md)).
The takeaways below follow directly from that result.

---

## Tier 1 — To *measure* a real edge (largely unblocked)

**Real historical closing odds.** The free NBA data has scores but no lines, so a
backtest brackets reality between a naive and an efficient market instead of
pricing against the real one. With actual closing odds, the bracket collapses to a
single number: **closing-line value (CLV)** and true post-vig ROI. As of the v4
work this is **loaded and measured locally** (DuckDB research store); the Postgres
served-model retrain is the only remaining step.

- **Source (confirmed working):** the **SBRO mirror**
  [`flancast90/sportsbookreview-scraper`](https://github.com/flancast90/sportsbookreview-scraper)
  ships `data/nba_archive_10Y.json`: pre-joined closing moneylines, **2011 to 2022,
  ~13.9k games, free**. (The original sportsbookreviewsonline.com bulk Excel 404s;
  the mirror is the practical path. ~2007-2011 is reachable from the classic SBRO
  Excel if needed; **no source covers pre-2007**.) For ongoing/clean lines, **The
  Odds API** (`ODDS_API_KEY`) reaches snapshots only from ~June 2020 at 10× credits,
  so use it to snapshot *future* closing lines near tip-off. Detail + caveats:
  [RESOURCES.md → Historical odds data](RESOURCES.md#historical-odds-data-for-clv).
- **Converter:** `sportsball-sbro-to-feed` (`pipelines/sbro_to_feed`) reshapes
  either the mirror JSON (`--format archive`) or the classic SBRO two-row Excel/CSV
  (`--format sbro --season-start-year`) into the `ingest_odds` feed, mapping terse
  team labels to canonical event ids.
- **Data-quality guard (done):** a single bad quote can flip backtest ROI from +29%
  to −6% (arXiv 2306.01740). `ingest_odds.passes_vig_guard` now rejects any line
  whose two-sided implied probs fall outside a sane vig band (`[1.01, 1.12]`). On the
  real archive it dropped 8 corrupt quotes (duplicated/garbage lines), zero false
  positives.
- **Ingest path (built + run):** `make ingest-odds FILE=...` populates
  `events.home_close`/`away_close` (Postgres), or `--duckdb data/sportsball.duckdb`
  writes the offline research store directly. The archive feed matched **12,505 of
  13,885 games** to canonical event ids (the ~1.4k misses are franchise renames:
  Hornets↔Pelicans, Bobcats↔Hornets, NJ↔Brooklyn).
- **Measured lift:** with odds in the DuckDB, `scripts/train_eval_duckdb.py` builds
  the `market_logit` feature and reports its out-of-sample holdout lift. On lined
  games log-loss improves 0.6506 → 0.6462 (+0.0044) and accuracy 0.6245 → 0.6361;
  blended over all test games (only ~32% lined) it is +0.0020. So `market_logit` is
  no longer inert; it carries real signal.
- **Remaining:** bring up Postgres (`make bootstrap` → `make ingest-odds` →
  `make retrain`) to activate `market_logit` in the *served* model, then `make clv`
  for real CLV. The shipped model artifacts are still stale v2.

---

> **Further reading:** [RESEARCH_NOTES.md](RESEARCH_NOTES.md) — what Medallion,
> the quant funds, market-makers, and pro betting syndicates (Benter, Starlizard)
> imply for this pipeline. The two highest-evidence upgrades it surfaces — *market
> price as a model feature* (Benter) and *CLV as the primary KPI* — map onto Tier 2
> below.

## Tier 2 — To *have* an edge over a sharp book (the hard truth)

A sharp book already prices everything our model knows — Elo, rest, back-to-backs,
season form. That is *exactly why* the efficient-book backtest found no +EV bets,
and why the feature ablation plateaued. Beating it needs information the market
hasn't fully priced:

1. **Injuries / availability (point-in-time).** "Who is actually playing tonight."
   The single highest-value missing signal — and the reason the roster feature was
   flat (it ignored availability). **Now wired:** `availability_diff` is the v4
   model's 8th feature, with `make ingest-injuries` deriving a leakage-free
   per-team-game availability score from the player logs (`team_availability_pit`),
   the trainer joining it, and the Engine reading tonight's value at serve. Inert
   (neutral 0) until availability data is loaded, then a retrain activates it — so
   the remaining lever is *data coverage / quality*, not plumbing.
2. **Lineup-level / late news.** Starting lineups, load management, trades — the
   late-breaking information that moves lines. (Feeds the availability score above.)
3. **Market microstructure & line shopping.** Line movement / steam, and
   **always betting the best available number across books**. For retail, best-line
   execution + arbitrage is a more reliable edge than out-predicting the closer.
   **Done:** the Engine now line-shops — when it decides to bet a side it prices
   and sizes against the best number any venue is offering on that team (the
   arbitrage book doubles as a best-line book), keeping the canonical event/side so
   settlement is unaffected. Steam/line-movement modeling is still open.
4. **Train against CLV, not just outcomes.** Optimize/select to beat the closing
   line; CLV is the leading indicator of genuine edge. (Gated on Tier 1 odds.)

> Blunt version: more aggregation of public box-score data will not do it — the
> ablation and the efficient-book result both confirm we've hit that ceiling.

---

## Tier 3 — To *run* it live for real (operational)

- **Live odds into the Engine.** The Oracle is mock-mode without a key; wire a real
  feed, and use the Scout's Polymarket prices as a second venue.
- **Durable persistence.** `make bootstrap` applies the schema, but the
  bind-mount/migration story is fragile — adopt proper migrations.
- **Real execution + risk controls.** Intentionally unimplemented (`EXECUTION_MODE=
  PAPER`). Do **not** enable until CLV is proven; live betting is regulated.
- **Live monitoring.** CLV and calibration-drift tracking so a decaying edge is
  caught early (the Slack digest/health agents are the hook).

---

## Lower-priority / breadth

- **Cross-venue arbitrage key** — *done*: `matching.matchup_key` gives an
  order-independent matchup key (sorted team tokens) and the arbitrage book is
  keyed by it with outcomes tracked by team token, so Oracle↔Polymarket prices
  meet regardless of home/away ([ARCHITECTURE §5](ARCHITECTURE.md#5-known-limitations)).
  Settling a reversed-orientation venue's own event row is the remaining edge case.
- **Multi-sport.** The Elo/feature/calibration machinery is sport-agnostic; only
  ingestion is NBA-specific.
- **Batched loads & nightly live-smoke CI** — `bootstrap`/`backfill-signals` insert
  row-by-row; the live integrations aren't in CI.

---

## Recommendation

If the goal is to learn whether this is **real**, Tier 1 dominates: get a
closing-odds feed and re-run the backtest against actual lines — that converts the
project from "plausible, well-characterized skill" to a measured yes/no. The
*plumbing* for the three highest-value items now exists — closing-odds ingest
(Tier 1), the point-in-time availability feature (Tier 2.1), and cross-book line
shopping + an order-independent arb key (Tier 2.3). What's left for each is **data,
not code**: real closing lines, and real injury/availability coverage. Feed those
in and re-run `make ingest-odds` → `make ingest-injuries` → `make retrain` →
`make clv` to measure whether the edge is real.
