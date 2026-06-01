# Roadmap: What Sportsball Needs

A prioritized, honest accounting of what the system needs — to *measure* a real
edge, to *have* one, and to *run* live. Grounded in what we've actually measured:
the model is well-calibrated and out-of-sample skillful (holdout Brier 0.220), it
beats a naive Elo-only book by a wide, season-robust margin, but against an
**efficient** book it finds **zero +EV bets** ([WHITEPAPER §5.4](WHITEPAPER.md)).
The takeaways below follow directly from that result.

---

## Tier 1 — To *measure* a real edge (the one hard blocker)

**Real historical closing odds.** Everything about "does it make money" is
currently unanswerable: the free NBA data has scores but no lines, so the backtest
brackets reality between a naive and an efficient market instead of pricing against
the real one. With actual closing odds, the bracket collapses to a single number —
**closing-line value (CLV)** and true post-vig ROI.

- **Need:** a paid odds feed (The Odds API, OddsJam, Rundown with a live key) or a
  historical closing-odds dataset. No robust free source exists for sharp lines.
- **Ingest path is built and waiting:** `make ingest-odds` (`pipelines/ingest_odds`)
  populates `events.home_close`/`away_close` from either an offline historical feed
  (`FILE=feed.json`, no key) or The Odds API (`ODDS_API_KEY`), with pure,
  unit-tested parsers (median consensus line across books). The moment odds land,
  `make clv` lights up and the backtest can price against real lines instead of the
  synthetic bracket in [`scripts/backtest.py`](../scripts/backtest.py).
- **Until then:** the *data* (not the plumbing) is the gating dependency for any
  monetary claim.

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
   flat (it ignored availability). **Now wired:** `availability_diff` is the v3
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
