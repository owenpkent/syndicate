# Quantitative Resources & Literature

This document serves as the theoretical foundation for **Project Sportsball**. The system is built upon industry-standard market logic, statistical forecasting, and financial risk management.

---

## ─── Foundational Literature ───

### 1. Market Microstructure & Logic
*   **"The Logic of Sports Betting" by Ed Miller & Matthew Davidow**
    *   *Significance:* The "Bible" for understanding how sharp market-makers operate. Foundational for our Oracle Agent's signal processing.
*   **"Moneyball: The Art of Winning an Unfair Game" by Michael Lewis**
    *   *Significance:* The genesis of the analytical revolution in sports. It highlights the importance of identifying undervalued assets (or discrepancies) where the market's perception differs from statistical reality.

### 2. Statistical Forecasting
*   **"Fixed Odds Sports Betting" by Joseph Buchdahl**
    *   *Significance:* Deep dive into **Closing Line Value (CLV)**. This logic is implemented in `src/sportsball/tools/clv.py` to quantify our statistical edge.
*   **"Statistical Sports Models in Excel" by Andrew Mack**
    *   *Significance:* High-level logic for regression and feature engineering. Mack's approach to Z-scores and sigma distributions informs our `advanced_models.py`.
*   **"Modelling Association Football Scores..." by Dixon and Coles (1997)**
    *   *Significance:* The definitive academic paper on using Poisson Distributions for sports modeling.

### 3. Quantitative Finance & Risk
*   **"Advances in Financial Machine Learning" by Marcos López de Prado**
    *   *Significance:* Critical for preventing "Backtest Overfitting," the primary failure mode of automated trading systems.
*   **"The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market" by Edward O. Thorp**
    *   *Significance:* The mathematical proof behind our `calculate_kelly_fraction` logic.

---

## ─── Industry Resources ───

### Data & Execution
*   **[The Rundown API v2](https://therundown.io/)**: Our primary source for sharp offshore market lines and historical results.
*   **[Polymarket Gamma API](https://gamma-api.polymarket.com/markets)** + **CLOB market channel** (`wss://ws-subscriptions-clob.polymarket.com/ws/market`): market discovery and live order-book feed for decentralized liquidity and prediction-market microstructure.

### Historical odds data (for CLV)

To backfill `events.home_close`/`away_close` and compute real closing-line value,
`pipelines/ingest_odds.py` takes either an offline file (`make ingest-odds FILE=...`)
or The Odds API (`ODDS_API_KEY`). **Status (Jun 2026): DONE, 2011-2026.** SBRO
mirror (2011-2022, free) + The Odds API historical backfill (2022-present,
`make backfill-odds-history`) → **17,338 priced games** in both Postgres and
DuckDB; the served v4 model is retrained on them and `market_logit` is active
(holdout log-loss 0.6308→0.6236). Ongoing lines stay current **for free** via
`make capture-odds` (LIVE endpoint ~1 credit/day, daily cron — fits the free
500/mo tier). We also did a one-time paid pull of **per-book h2h + totals** into
DuckDB `odds_quotes` (248k quotes, 23 books) for line-shopping + a totals model.
First real **CLV: −1.67% (sub-par)** — good predictor, doesn't beat the close.
Researched source comparison:

| Source | Closing lines? | Depth | Access | Cost | Notes |
|---|---|---|---|---|---|
| **SBRO mirror** (`flancast90/sportsbookreview-scraper`) | Yes (closing ML) | 2011→2022 (~13.9k games) | `data/nba_archive_10Y.json` | **free** | **Used.** Pre-joined per-game JSON; convert with `sportsball-sbro-to-feed --format archive`. The original sportsbookreviewsonline.com bulk Excel 404s (classic two-row format still supported via `--format sbro`). Kaggle `ehallmar/nba-historical-stats-and-betting-data` is an alternative (needs auth). |
| **The Odds API** | Live + historical snapshots | **from ~Jun 2020** | REST | free 500/mo (live); historical **10 cr/market/call** | **Used.** Historical backfilled 2022-present (~13k cr); per-book h2h+totals pulled to `odds_quotes`. Ongoing capture is ~1 cr/day → free tier. |
| **TheRundown** | Dedicated closing endpoints (NBA sport id 4) | ~2020 (plan-dependent) | REST | had a trial key | **Verified insufficient:** trial tier returns events with **no odds lines** and rate-limit 1 — not usable without a paid plan. |
| **SportsDataIO** | Yes | from 2019 | REST | paid | — |
| **OddsJam** | Yes | unspecified | REST | est. ~$500–1000/mo, no public price | — |

*   **No free source covers pre-2007**, so a real CLV backtest tops out at
    ~2007 to present, not the full 1983 history. The mirror in use starts 2011.
*   **Data quality is load-bearing (guard implemented):** one bad quote flipped a
    published backtest from +28.8% to -6.3% ROI
    ([arXiv 2306.01740](https://arxiv.org/abs/2306.01740)). `ingest_odds.passes_vig_guard`
    now rejects any line whose two-sided implied probabilities fall outside a sane
    vig band (`[1.01, 1.12]`) before persisting; it dropped 8 corrupt quotes from the
    real archive with zero false positives.
*   Recommendation: **SBRO mirror for free deep history; The Odds API for clean
    ongoing lines.** See [ROADMAP.md Tier 1](ROADMAP.md).

### Line-movement archives (opening + closing) — multi-sport, free

The same SBRO scraper ships **10-year archives with OPENING and CLOSING lines**
(moneyline / spread / total) for **NBA, MLB, NHL, NFL** — the raw material for the
line-movement (steam) edge, since it carries both ends of the move:
`raw.githubusercontent.com/flancast90/sportsbookreview-scraper/main/data/{nba,mlb,nhl,nfl}_archive_10Y.json`
(consensus, not per-book). Loaded to `data/*_archive_10Y.json`; `scripts/steam_validation.py`
and `scripts/predict_close_experiment.py` run on them. **Per-book intraday history
(needed for book lead-lag) is NOT freely available** — that requires the live
`capture-quotes` capture at intraday frequency.

### Communities & Podcasts
*   **"Circles Off" Podcast**: Expert-level discussion on high-stakes quantitative betting and steam tracking.
*   **Pinnacle "Betting Resources"**: Academic-grade articles on market efficiency and predictive modeling.
