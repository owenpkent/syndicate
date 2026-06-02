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
