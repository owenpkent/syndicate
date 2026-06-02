# research/ — cross-market edge exploration

Exploratory analyses that go **beyond the sports model** — testing the project's
core methodology (find a structural inefficiency → validate honestly → backtest
with realistic execution) on other markets. Kept separate from `scripts/`
(production tooling) and `src/` (the package) so exploration stays tidy.

Findings are condensed into the `edge-research` memory and `docs/ROADMAP.md`.

| File | Market | What it asks |
|------|--------|--------------|
| `polymarket_scan.py` | Polymarket prediction markets | Characterize markets (vig/spread/liquidity) + cross-venue divergence vs the sharp sportsbook line (same MLB games) — does Polymarket misprice relative to a sharper book? |
| `log_polymarket_divergence.py` | Polymarket vs sharp book | Accumulating log of pre-game Polymarket-vs-sharp divergence (MLB) → `polymarket_divergence`; does buying the poly-cheap side on a >X% gap win? |
| `defi/` | **DeFi time-series** (Hyperliquid perps, CEX spot, Polymarket crypto books) | The pivot toward time-series prediction on decentralized finance — dense microstructure snapshots → `data/defi.duckdb`, plus CEX↔DEX lead-lag. See `defi/README.md`. |

## Context — the pivot to DeFi time-series
The real goal is honing **time-series prediction**, target domain **decentralized
finance**; sports/prediction markets were scaffolding. `defi/` collects a dense,
continuous, microstructured substrate (on-chain perps + CEX spot + on-chain
prediction books) — a far better forecasting gym than ~1 settled outcome per game
per day. The two ideas that survived the sports edge hunt (book lead-lag, the
market-line-as-input lever) transfer directly to crypto venues.

## Context — why prediction markets
The sports edge hunt concluded: no capturable edge except possibly book lead-lag
(gated on data). Prediction markets (Polymarket/Kalshi) are **less efficient**,
have **free data + programmatic execution + no winner bans**, and the repo already
integrates Polymarket (`src/sportsball/markets/polymarket.py`). The edge is
**judgment/modelling-driven, not a speed race** — the best remaining fit for an
analytical, retail approach. See `docs/ROADMAP.md` recommendation.
