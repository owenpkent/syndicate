# DeFi time-series collectors

The DeFi pivot of the project. The goal is honing **time-series prediction**; the
target domain is decentralized finance (on-chain perps, prediction markets,
DEX/CEX microstructure). Sports odds were the scaffolding.

All collectors follow the sports-cron idiom (`scripts/capture_snapshot.py`): pull a
live snapshot, append rows keyed by `captured_at`, log one line. They write to a
**separate** store — `data/defi.duckdb` — so their dense cadence never contends
with the sports odds writers. Free, no API keys.

| Script | Tables | What it captures |
|---|---|---|
| `capture_hyperliquid.py` | `hl_ctx`, `hl_book` | Decentralized perps. `hl_ctx`: funding, OI, mark/oracle/mid px, premium, 24h vol for **all ~230 coins** (one call → doubles as the funding/OI metrics panel). `hl_book`: top-10 L2 levels per side for a majors subset (depth/spread/imbalance). |
| `capture_cex.py` | `cex_spot` | Coinbase + Kraken spot (Binance is geo-blocked / HTTP 451 here). Ground truth to score on-chain predictions, and the CEX leg of lead-lag. Canonical `asset` column joins straight to `hl_ctx.coin`. |
| `capture_polymarket_book.py` | `pm_book` | Real CLOB order book (best bid/ask, mid, depth) for the most-liquid **crypto** markets ("Will BTC be above $X on <date>"). On-chain-settled prediction series whose truth is a price we already capture. |

**Lead-lag is not a separate collector** — capture `hl_ctx` (mark) and `cex_spot`
(last) at the same cadence, then join on `asset` + nearest `captured_at` to ask
which venue moves first and by how long.

## Historical backfill

The live collectors only see the present. `backfill_history.py` seeds weeks of
history in one shot (free, no keys) so the notebooks have real numbers immediately
instead of waiting on the cron. It writes **separate history tables** (candles /
time series, distinct from the live point-in-time snapshots above):

| Source | Table | What backfills |
|---|---|---|
| Hyperliquid candles | `hl_candles` | 1-min OHLCV (mark/price proxy), per coin |
| Hyperliquid funding | `hl_funding_hist` | hourly funding rate + premium, per coin |
| Coinbase candles | `cex_candles` | spot OHLCV (60s default, paginated) |
| Polymarket history | `pm_price_hist` | per-market mid time series (open + resolved crypto) |
| Polymarket resolved | `pm_resolved` | resolved crypto markets **with settled outcome** (`outcomePrices`) — the calibration ground truth |

**What does NOT backfill: order-book depth.** `hl_book` / `pm_book` spreads and
depth come only from live snapshots — there's no historical L2 endpoint. Price,
funding, and resolved outcomes all backfill; microstructure depth does not.

```bash
python research/defi/backfill_history.py                 # 14d, default coins
python research/defi/backfill_history.py --days 30 --coins BTC,ETH,SOL
python research/defi/backfill_history.py --only polymarket
```

`backfill_history` is a **one-shot seed** (idempotent — `ON CONFLICT DO NOTHING`),
not a cron; re-run it to extend the window. The cron collectors keep the live
tables current going forward.

## Analysis

`analyze_leadlag.py` (read-only) reports, per asset:
- **basis** — Hyperliquid mark vs CEX spot (mean / std / latest), plus the
  inter-CEX (Coinbase vs Kraken) spread as a sanity floor: a basis much larger
  than the inter-CEX spread is a real venue dislocation, not quote noise.
- **lead-lag** — on a common time grid, the lag that maximizes HL-return vs
  CEX-return correlation. Positive lag ⇒ HL leads (CEX follows). Needs ~22 grid
  points (~110 min at 5-min cadence); reports basis only until then.

```bash
python research/defi/analyze_leadlag.py
python research/defi/analyze_leadlag.py --grid 5 --max-lag 6 --venue coinbase
```

## Run

```bash
python research/defi/capture_hyperliquid.py            # all coins ctx + majors book
python research/defi/capture_cex.py                    # Coinbase + Kraken spot
python research/defi/capture_polymarket_book.py        # top-30 crypto prediction books
```

## Suggested cron (free, no keys)

```cron
# Hyperliquid + CEX every 5 min, same minute (lead-lag wants simultaneous reads;
# the DuckDB lock retry resolves the rare write collision).
*/5 * * * * cd /home/owen/Documents/dev/sportsball && ./venv/bin/python3 research/defi/capture_hyperliquid.py >> data/defi_capture.log 2>&1
*/5 * * * * cd /home/owen/Documents/dev/sportsball && ./venv/bin/python3 research/defi/capture_cex.py >> data/defi_capture.log 2>&1
# Polymarket books move slower — every 30 min.
*/30 * * * * cd /home/owen/Documents/dev/sportsball && ./venv/bin/python3 research/defi/capture_polymarket_book.py >> data/defi_capture.log 2>&1
```
