# Quantitative Handbook: Project Sportsball

This document provides a deep dive into the mathematical and statistical foundations of the Sportsball's trading logic.

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

---

## 4. Cross-Market Arbitrage

Beyond statistical edge ($EV$), the Sportsball detects mathematical "lock" opportunities where discrepancies between venues allow for guaranteed profit.

### The Arbitrage Equation
An opportunity exists when the sum of the implied probabilities for all mutually exclusive outcomes of an event is less than 1.0:

$$S = \sum_{i=1}^{n} \frac{1}{\text{Odds}_i} < 1$$

The **Profit Margin** ($M$) is calculated as:
$$M = (1 - S) \times 100\%$$

### Multi-Leg Execution
When $S < 1$, the engine constructs an `ARBITRAGE` signal requiring simultaneous execution of all $n$ legs. The capital is allocated to ensure an equal return regardless of the outcome:

$$\text{Allocation}_i = \frac{1/\text{Odds}_i}{S}$$

*Example:* A $100 budget on an arbitrage with $S=0.95$ results in a guaranteed return of $105.26 ($5.26 profit).
