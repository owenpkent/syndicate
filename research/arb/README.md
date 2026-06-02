# research/arb — arbitrage candidate scanner

A **monitor, not an executor.** Scans markets we already collect for riskless-by-
construction arbitrage candidates, with honest fillability checks. No event-matching,
no model — just price relationships that must hold.

```bash
python research/arb/scan.py                          # both sources, 1% buffer
python research/arb/scan.py --source polymarket --buffer 0.0 --min-size 50
python research/arb/scan.py --slack                  # also post to SLACK_WEBHOOK_URL
```

## What it checks
- **Sportsbook cross-book** (`data/sportsball.duckdb` `odds_snapshots`): per game, the
  best decimal price for each side across all ~9 books; if `1/best_A + 1/best_B < 1`,
  backing both sides is a guaranteed profit. *Caveat: snapshots are up to ~2h stale and
  the best price is often a soft/offshore book (limits, voids).*
- **Polymarket multi-outcome** (Gamma + CLOB): within a mutually-exclusive **neg-risk**
  event (every temperature bucket, every candidate, …), exactly one resolves YES — so if
  the best-ask of *every* outcome sums to < 1, buy them all for a guaranteed $1.
  (A single binary Yes/No market can't arb — No is Yes's complement, so the asks sum to
  ≥ 1 by construction.)

## The honest pipeline (why most "arbs" are fake)
Gamma's cached `bestAsk` is **stale** — it flags many sum-to-<1 candidates that don't
exist on the live book. So each candidate is **verified against the live CLOB order
book**, and filtered by `--min-size` (fillable depth on the thinnest leg). What survives:
- *phantom* (a best-ask level with **0 size**) → dropped.
- *negligible* (margin < buffer, or a few shares) → dropped.
- *real* → e.g. a 4-outcome event summing to 0.97 (3%) with ~150 shares of depth —
  genuine but **tiny and fleeting**.

This is the whole lesson made concrete (see `market-efficiency-survey` memory): real arbs
live only in thin/niche multi-leg markets, are small, and evaporate on close inspection.
Liquid venues are bot-arbed in milliseconds — a cron sees the leftovers. Useful as an
alerting monitor; not a money printer. (`EXECUTION_MODE` stays paper — this never trades.)
