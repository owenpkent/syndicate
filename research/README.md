# research/ — cross-market edge exploration

Exploratory analyses that go **beyond the sports model** — testing the project's
core methodology (find a structural inefficiency → validate honestly → backtest
with realistic execution) on other markets. Kept separate from `scripts/`
(production tooling) and `src/` (the package) so exploration stays tidy.

Findings are condensed into the `edge-research` memory and `docs/ROADMAP.md`.

| File | Market | What it asks |
|------|--------|--------------|
| `polymarket_scan.py` | Polymarket prediction markets | Characterize markets (vig/spread/liquidity) + cross-venue divergence vs the sharp sportsbook line (same MLB games) — does Polymarket misprice relative to a sharper book? |

## Context — why prediction markets
The sports edge hunt concluded: no capturable edge except possibly book lead-lag
(gated on data). Prediction markets (Polymarket/Kalshi) are **less efficient**,
have **free data + programmatic execution + no winner bans**, and the repo already
integrates Polymarket (`src/sportsball/markets/polymarket.py`). The edge is
**judgment/modelling-driven, not a speed race** — the best remaining fit for an
analytical, retail approach. See `docs/ROADMAP.md` recommendation.
