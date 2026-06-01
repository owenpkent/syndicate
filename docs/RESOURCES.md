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
or The Odds API (`ODDS_API_KEY`). Researched source comparison (May 2026):

| Source | Closing lines? | Depth | Access | Cost | Notes |
|---|---|---|---|---|---|
| **SBRO** (sportsbookreviewsonline.com) | Yes (ML + spread/total) | ~2007→present | bulk Excel → CSV/JSON | **free** | **Deepest free option.** Direct URL has 404'd; use a maintained mirror/scraper (e.g. `FinnedAI/sportsbookreview-scraper`) or the Kaggle set `ehallmar/nba-historical-stats-and-betting-data`. |
| **The Odds API** | Live + historical snapshots | **from ~Jun 2020** | REST | free tier live-only; historical paid, **10× credits** | Already wired. Best for snapshotting *future* closing lines near tip-off. |
| **TheRundown** | Dedicated closing endpoints (NBA sport id 4) | ~2020 (plan-dependent, **unverified**) | REST | have a key | Verify actual endpoint paths (`docs.therundown.io/llms.txt`) + plan depth before relying on it. |
| **SportsDataIO** | Yes | from 2019 | REST | paid | — |
| **OddsJam** | Yes | unspecified | REST | est. ~$500–1000/mo, no public price | — |

*   **No source covers pre-2019** — a real CLV backtest realistically tops out at
    ~2007→present (free), not the full 1983 history.
*   **Data quality is load-bearing:** one bad quote flipped a published backtest from
    +28.8% to −6.3% ROI ([arXiv 2306.01740](https://arxiv.org/abs/2306.01740)) — always
    sanity-check that a game's two-sided implied probabilities sum to a plausible vig
    (~1.02–1.10) before persisting.
*   Recommendation: **SBRO/mirror for free deep history; The Odds API for clean
    ongoing lines.** See [ROADMAP.md Tier 1](ROADMAP.md).

### Communities & Podcasts
*   **"Circles Off" Podcast**: Expert-level discussion on high-stakes quantitative betting and steam tracking.
*   **Pinnacle "Betting Resources"**: Academic-grade articles on market efficiency and predictive modeling.
