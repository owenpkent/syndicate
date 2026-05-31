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

### Communities & Podcasts
*   **"Circles Off" Podcast**: Expert-level discussion on high-stakes quantitative betting and steam tracking.
*   **Pinnacle "Betting Resources"**: Academic-grade articles on market efficiency and predictive modeling.
