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
| `defi/` | **DeFi time-series** (Hyperliquid perps, CEX spot, Polymarket books) | Dense microstructure snapshots → `data/defi.duckdb` (+ `backfill_history.py`), CEX↔DEX lead-lag, crypto returns-vs-volatility predictability, and broad Polymarket calibration. See `defi/README.md`. |
| `mlb/` | **MLB** (free MLB Stats API) | Baseball win-prob: Elo+features on 38k games + a real FIP starting-pitcher rating. ~56%; pitcher identity barely helps (variance dominates). See `mlb/README.md`. |
| `nhl/` | **NHL** (free NHL web API) | Hockey win-prob: binary Elo on 18k games. ~56%; ceiling = starting goalie. See `nhl/README.md`. |
| `wc/` | **World Cup / soccer** (free intl-results CSV) | 3-class (W/D/L) Elo on 49k internationals; ~51% on World-Cup holdout; predicts 2026. See `wc/README.md`. |
| `kalshi/` | **Kalshi** (regulated prediction market) | Authenticated API access (RSA signing). Data access poor for research; kept as infra. See `kalshi/README.md`. |
| `arb/` | **Arbitrage scanner** (sportsbook, Polymarket, crypto) | `scan.py` flags riskless candidates (cross-book, Polymarket neg-risk, CEX↔CEX spot) — CLOB-verified, fillability-filtered — plus crypto **funding carry**. Monitor, never trades. See `arb/README.md`. |

Visualizations for all of the above live in `../notebooks/` (auto-rendered daily to a
dark HTML dashboard). Cross-market findings are condensed into the `market-efficiency-survey`,
`crypto-predictability`, `mlb-model`, `nhl-model`, `wc-model`, and `polymarket-exploration` memories.

## The headline finding — liquidity = efficiency
A broad search (sports books, crypto price, Polymarket across categories, dYdX perps,
Kalshi) converged on one conclusion: **every market is efficient where it's liquid**, and
"inefficiency" only appears where it's too thin to trade (dYdX long-tail: real funding
dislocation, ~$0 volume) or needs infrastructure rather than a model (CEX↔DEX speed,
cross-venue matching). The prediction *craft* pays on **un-priced signal** — sports
*outcomes* (NBA 66% / MLB·NHL ~56% / WC ~51% 3-way), crypto *volatility* (R²≈0.42) — not
on beating liquid market *prices* (sharp closing lines, BTC direction, calibrated Polymarket).

**The one real, capturable edge surfaced: crypto funding carry** (`arb/scan.py`,
`notebooks/12_funding_carry`). Hyperliquid perps run persistent annualized funding a
delta-neutral hold collects (e.g. XMR ~+41%/yr at 0.98 sign-stability). It is NOT riskless
(basis, funding-flip, spot-borrow, fees, thin-alt capacity) and the headline rates are
often spikes — rank by mean × sign-stability, then **net of costs** (`notebooks/13`).

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
