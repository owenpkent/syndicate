# lob-engine

A low-latency **market-data + order-book + replay** engine, built to learn (and
demonstrate) the systems side of quant infrastructure. The Rust crates own the
latency-critical spine; an OCaml strategy layer (added later) owns the
correctness-critical trading logic — the same division of labor a firm like Jane
Street uses.

> Lives inside the `sportsball` research repo for now (it reuses the existing
> Hyperliquid/CEX collectors and `data/` stores). When polished it can be lifted
> into a standalone repo with `git subtree split`, preserving its own history.

## Architecture

```
   exchange WS ─▶  collector  ─▶  tape (normalized binary log)
                                    │
                                    ▼
                          book engine (reconstruction)   ── planned
                                    │
                                    ▼
                          replay engine (event-driven)   ── planned
                                    │  typed event/fill stream
                                    ▼
                          OCaml strategy (A–S quoting)    ── planned
```

## Crates

| Crate | Status | What it does |
|---|---|---|
| `tape` | ✅ | Normalized append-only binary market-data log (order-book snapshots + trades). Hand-rolled little-endian codec, no serde — explicit wire format, bounds-checked decode. Unit-tested roundtrip + corruption handling. |
| `collector` | ✅ (v1) | Hyperliquid WebSocket → tape. Subscribes `l2Book` + `trades`, reconnects with backoff, logs throughput + an ingest-latency proxy. |
| `book` | ⏳ | Order-book reconstruction engine (efficient price-level structures, gap detection, snapshot resync). The centerpiece. |
| `replay` | ⏳ | Deterministic event-driven backtest/replay: simulated latency, queue-aware fills, fees. |

## Build & run

```bash
cargo build --workspace
cargo test  --workspace                                  # tape codec + book tests
cargo run -p collector -- --coins BTC,ETH,SOL --secs 10 --out /tmp/probe.tape
cargo run -p tape --example dump -- /tmp/probe.tape       # inspect a captured tape
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

## Roadmap (the SWE portfolio arc)

1. **Data spine** — collector + tape, with measured ingest latency/throughput. ← *here*
2. **Book engine** — reconstruct full L2 from a delta-streaming venue (Coinbase
   `level2`); benchmark p50/p99 update latency, property-test book invariants.
3. **Replay engine** — replay the tape through a `Strategy` trait, realistic fills.
4. **Strategy (OCaml)** — Avellaneda–Stoikov market-maker; decompose PnL into
   spread captured vs adverse selection vs inventory.

Performance numbers and flamegraphs land in this README as each stage ships.
