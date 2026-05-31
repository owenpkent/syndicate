# Quantitative Handbook: Project Syndicate

This document provides a deep dive into the mathematical and statistical foundations of the Syndicate's trading logic.

---

## 1. Core Logic: The Value Proposition

The system evaluates every market signal through the lens of **Expected Value ($EV$)**.

### Expected Value ($EV$)
The market price of a binary contract or line represents an implied probability ($P_{\text{market}}$):

$$P_{\text{market}} = \frac{1}{\text{Decimal Odds}}$$

A trade is only considered if the system's computed **True Probability ($P_{\text{true}}$)** exceeds the market pricing:

$$EV = (P_{\text{true}} \times \text{Odds}) - 1$$

*Threshold:* The system enforces a default `safety_buffer_ev` (e.g., 0.02) to account for model variance.

### Bankroll Management (Fractional Kelly)
To maximize logarithmic growth while protecting against bankruptcy, we use the **Kelly Criterion**:

$$f^* = \frac{EV}{\text{Odds} - 1}$$

To mitigate estimation error, we apply a **Kelly Multiplier ($c$)**:

$$f_{\text{actual}} = c \times f^*$$

*Standard:* We typically use a Quarter-Kelly ($c=0.25$).

---

## 2. Advanced Quantitative Arsenal

### Logistic Regression
Used to derive $P_{\text{true}}$ from raw statistical features.
*   **Input:** Multi-dimensional feature arrays (e.g., [Home_ELO, Away_ELO, Rest_Differential]).
*   **Output:** A calibrated probability of the binary outcome (Home Win).
*   **Implementation:** `sklearn.linear_model.LogisticRegression`.

### Gradient-Optimized Rating Systems
A system to autonomously tune rating parameters (like Elo) against historical results.
*   **Objective Function:** Log-Loss minimization.
*   **Optimization Algorithm:** L-BFGS-B (via `scipy.optimize.minimize`).
*   **Optimized Parameters:** Base K-factors, Home Field Advantage, and mean-reversion constants.

### Monte Carlo Simulation
Used to price derivative markets (Alternative Spreads, Over/Unders) where analytical solutions are complex.
*   **Method:** 10,000+ iterations of scoring simulations using `numpy.random`.
*   **Distributions:** Derived from offensive/defensive efficiency matrices and historical volatility.

---

## 3. Scoring Distributions (Poisson)

For discrete scoring environments (Soccer, Team Totals), we model the number of scoring events ($k$) given a mean rate ($\lambda$):

$$P(X=k) = \frac{\lambda^k e^{-\lambda}}{k!}$$

The engine aggregates these into a joint matrix to evaluate spreads and total lines.
