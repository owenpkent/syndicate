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
- **Work once we have it:** ingest closing odds into `events.home_close`/`away_close`
  (the columns already exist), then a real CLV backtest replaces the synthetic
  bracket in [`scripts/backtest.py`](../scripts/backtest.py). `make clv` lights up.
- **Until then:** this is the gating dependency for any monetary claim.

---

## Tier 2 — To *have* an edge over a sharp book (the hard truth)

A sharp book already prices everything our model knows — Elo, rest, back-to-backs,
season form. That is *exactly why* the efficient-book backtest found no +EV bets,
and why the feature ablation plateaued. Beating it needs information the market
hasn't fully priced:

1. **Injuries / availability (point-in-time).** "Who is actually playing tonight."
   The single highest-value missing signal — and the reason the roster feature was
   flat (it ignored availability). The only real modeling lever left.
2. **Lineup-level / late news.** Starting lineups, load management, trades — the
   late-breaking information that moves lines.
3. **Market microstructure & line shopping.** Line movement / steam, and
   **always betting the best available number across books**. For retail, best-line
   execution + arbitrage is a more reliable edge than out-predicting the closer.
4. **Train against CLV, not just outcomes.** Optimize/select to beat the closing
   line; CLV is the leading indicator of genuine edge.

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

- **Cross-venue arbitrage key** — an order-independent matchup key so Oracle↔
  Polymarket `event_id`s align regardless of home/away ([ARCHITECTURE §5](ARCHITECTURE.md#5-known-limitations)).
- **Multi-sport.** The Elo/feature/calibration machinery is sport-agnostic; only
  ingestion is NBA-specific.
- **Batched loads & nightly live-smoke CI** — `bootstrap`/`backfill-signals` insert
  row-by-row; the live integrations aren't in CI.

---

## Recommendation

If the goal is to learn whether this is **real**, Tier 1 dominates: get a
closing-odds feed and re-run the backtest against actual lines — that converts the
project from "plausible, well-characterized skill" to a measured yes/no. Short of
a feed, the highest-value modeling work is **point-in-time injuries/availability**
(Tier 2.1) and the highest-value engineering work is **cross-book line shopping**
(Tier 2.3). Everything else is breadth, not edge.
