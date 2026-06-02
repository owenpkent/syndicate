# lob-engine

A native-Rust **trading-simulator game with an AI strategy coach** — built on real
market microstructure. You replay a real captured order-book tape, trade against the
reconstructed book, and an embedded AI coach reads the live market + your position and
advises you. Under the hood it's also a genuine low-latency market-data / order-book /
replay engine — the systems side of quant infrastructure, the same division of labor a
firm like Jane Street uses.

> Lives inside the `sportsball` research repo for now (it reuses the existing
> Hyperliquid/CEX collectors and `data/` stores). When polished it can be lifted
> into a standalone repo with `git subtree split`, preserving its own history.

## The game

```
  exchange WS ─▶ collector ─▶ tape ─▶ replay ─▶ book (the ladder you watch)
                                         │
                                         ▼
                                  sim: your position / cash / PnL, your fills
                                         │
                                         ▼
                                  TUI (ratatui): ladder + chart + position
                                         │  "what should I do here?"
                                         ▼
                                  agent: AI strategy coach (read_book,
                                         my_position, recent_trades tools)
```

## Crates

| Crate | Status | What it does |
|---|---|---|
| `tape` | ✅ | Normalized append-only binary market-data log (order-book snapshots + trades). Hand-rolled little-endian codec, no serde — explicit wire format, bounds-checked decode. Unit-tested roundtrip + corruption handling. |
| `collector` | ✅ | Hyperliquid WebSocket → tape. Subscribes `l2Book` + `trades`, reconnects with backoff, logs throughput + an ingest-latency proxy. |
| `book` | ✅ | L2 order-book reconstruction from snapshot + sequenced deltas: fixed-point ticks, gap detection + resync, best/mid/microprice/spread. 5 unit tests + a proptest on book invariants. |
| `agent` | ✅ | Native AI-agent runtime — hand-rolled Anthropic Messages API client + tool-use loop (the strategy coach). Tool trait + `bash`/`read_file` starter tools + CLI. 5 wire-format/dispatch tests. Needs `ANTHROPIC_API_KEY`. |
| `replay` | ⏳ | Steps a tape through sim-time into the book; deterministic, simulated latency, fees. The game clock. |
| `sim` | ⏳ | Player state: position, cash, realized/unrealized PnL, drawdown; matches your orders against the book. |
| `tui` | ⏳ | The playable interface (ratatui): order-book ladder, price chart, position panel, and an "ask the coach" prompt wired to `agent` + market tools. |

## Build & run

```bash
cargo build --workspace
cargo test  --workspace                                  # tape + book + agent tests
cargo run -p collector -- --coins BTC,ETH,SOL --secs 10 --out /tmp/probe.tape
cargo run -p tape --example dump -- /tmp/probe.tape       # inspect a captured tape
ANTHROPIC_API_KEY=sk-ant-... cargo run -p agent -- "do the crates build?"  # AI coach (CLI)
```

## Measured (v1 data spine)

Live Hyperliquid capture, BTC/ETH/SOL, `l2Book` + `trades`:

| Metric | Value |
|---|---|
| Throughput | ~42 events/s (18.5 KiB/s) on 3 symbols |
| Tape round-trip | ✅ 298 events (54 books, 244 trades) decoded, best bid/ask intact |
| Codec | 0 allocations beyond the single frame buffer |

The current ingest-latency line is a coarse proxy (`local_recv − venue_event_time`),
inflated by Hyperliquid's snapshot cadence; a tighter per-message latency lands with the
delta-streaming venue in the book stage. Numbers here are throughput/correctness, not yet
a tuned hot path — the *measure-then-optimize* loop starts at the book engine.

## Roadmap (toward the playable game)

1. **Data spine** — collector + tape, with measured ingest latency/throughput. ✅
2. **Book engine** — L2 reconstruction, gap detection, proptest invariants. ✅
3. **AI coach** — native Rust agent runtime (Anthropic client + tool loop). ✅
4. **Replay engine** — step a tape through sim-time into the book (the game clock);
   deterministic, simulated latency, fees. ← *next*
5. **Sim** — player position / cash / PnL; match player orders against the book.
6. **TUI (ratatui)** — order-book ladder + price chart + position panel + an
   "ask the coach" prompt wired to `agent` with `read_book` / `my_position` /
   `recent_trades` tools. **Now it's playable.**
7. **Polish** — difficulty/benchmarks (vs buy-hold and vs the coach's picks),
   p50/p99 engine benchmarks + flamegraphs, more venues/symbols.

Numbers and flamegraphs land in this README as each stage ships.
