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

### Edge research — what we actually found (2026-06, real odds 2011-2026)

With closing odds loaded and **248k per-book quotes** (23 books, h2h + totals,
2022-2026) we hunted for a real edge against actual outcomes. The honest result:
**no capturable edge in historical closing snapshots at realistic execution.**

| Approach | Result |
|---|---|
| Model beats the close (sides) | No — CLV **−1.67%**, beat-rate 53% |
| Model beats the close (totals) | No — residual OOS R² **≈ 0** (linear *and* GBM, with pace/eff/form/rest) |
| Line-shopping alone | ~breakeven — erases the vig, not an edge |
| Soft-book +EV vs consensus (h2h) | Real **+10–15% in 2022–24**, **decayed to −19% in 2025** |
| Soft-book +EV vs consensus (totals) | Looked huge (+16%, t≈10, no decay) but is **stale-line artifact**: bets averaged 7.8 pts off consensus; restricted to a bettable 2–4 pt gap → **+0.3%, ≈ zero** |

The robust through-line: **every apparent edge lived in stale / outlier quotes
you cannot actually bet** (the absolute-best price, the 4–15 pt-off totals line).
Strip them out and the market is efficient w.r.t. our data. This is the same
result as the efficient-book backtest, now confirmed on real multi-book prices.
(See the `edge-research` memory and `tools/clv.py`.)

**The one real edge: line *movement* (steam).** The SBRO archive
(`data/nba_archive_10Y.json`, already on disk) carries **opening and closing**
spreads + totals (2011-2022). Betting the side the total *moved toward*, at the
**opening** consensus line, settled vs actual: **move≥1 → 57.7% win, +10% ROI;
move≥4 → 64%, +23%** (monotonic, ~10k bets). This is genuine, not a stale-line
artifact — the opener is a real bettable line and the edge is the line *moving*
(the close is provably sharper than the open). **Caveat: our model cannot predict
the move** (OOS R² ≈ 0 vs *both* open and close — opener MAE 14.80 ≈ close 14.42);
the move is driven by sharp money / news we don't have. So it is **not predictable
from our features — only observable in real time.** The deployable form is
**steam-chasing**: watch a line move, bet the moving side at a number still better
than close, capturing a *fraction* of the full move. `make capture-quotes`
(open/close, free daily cron) is the data hook; real-time chasing wants a few
**intraday** snapshots too.

**This edge is sport-agnostic — now validated on ~56k games across 4 sports**
(`scripts/steam_validation.py`, free SBRO 10Y archives nba/mlb/nhl/nfl). Following
the total's move at the open wins 53-60%+ with positive ROI in *every* sport (NBA,
MLB, NHL, NFL) — confirming it's a market-structure law (the close is sharper than
the open *everywhere*), not a basketball or data-artifact effect. So it can
be deployed on whatever is *in season* (the Odds API `/sports` list shows MLB,
WNBA, FIFA World Cup, NHL all active in June), rather than waiting for the NBA
season. **WNBA** reuses the basketball model directly; **MLB** offers the most
daily volume. Gated on **time** (a season of live capture), not money.

### Modeling the market (not the game) — the strategic pivot

The game is efficiently priced; the *price-formation process* is not. Stop
predicting `P(outcome)` (proven dead) and model how the price *behaves*. Four
exploitable structures:

1. **Temporal — line movement / steam.** The close is provably sharper than the
   open, so the move's *direction* is signal (validated: +10–23% ROI). Reverse
   line movement (line moves against public %) is the canonical version.
2. **Cross-sectional — book lead-lag.** Sharp books (Pinnacle, Circa) move first;
   recreational books follow minutes later. Beating the laggard to the number
   captures the move with *certainty*, not prediction. **The cleanest deployable
   edge** — but it needs **intraday per-book time-series**, which no free archive
   publishes (the SBRO archives are consensus open/close only). So it is gated on
   the live capture building up *with intraday frequency* — the one thing that
   would benefit from a cheap paid tier; cannot be shortcut with historical data.
3. **Behavioral — public bias.** Recreational money over-backs favorites, overs,
   and popular teams; books shade lines accordingly, leaving the unpopular side a
   hair cheap. Fade the bettors, not the teams.
4. **Microstructure — timing/execution.** *When* (open, off-hours) and *where*
   (obscure markets) a line is softest; getting down before it moves.

All of these are **CLV-generating machines**: you systematically obtain a price
the market later moves past. CLV — not P&L — is the scorecard (significant in tens
of bets, not thousands).

**First measured result (`scripts/predict_close_experiment.py`, totals 2011-2022):**
predicting the *game* gave OOS R² ≈ 0, but predicting the **line's move**
(close−open) from open-time pace/efficiency gives **OOS R² = +0.016 (corr +0.13)** —
positive, because the opener under-prices current-season pace that the close later
adds. Betting the opener when our predicted-close diverges >1pt: 61% win, +17% ROI,
**CLV +2.4 pts** (n=83, thin but directional). The edge is *small and real* where
game-modeling was *zero*. Next: scale the sample on the live multi-sport capture,
and build **lead-lag** detection (structure #2) from intraday per-book snapshots —
the genuine, deployable research direction.

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
  ingestion is NBA-specific. **Now a near-term lever, not just breadth:** the steam
  edge (above) is sport-agnostic and the Odds API already covers in-season sports
  (MLB, WNBA, World Cup, NHL). Pointing `capture-quotes` at an in-season sport
  starts the live steam dataset *today* instead of waiting for the NBA tip-off.
  WNBA reuses the basketball model as-is; MLB/soccer need their own features but
  the line-movement capture needs none.
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
