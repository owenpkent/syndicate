import numpy as np
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
import logging

logger = logging.getLogger("AdvancedModels")

class LogisticWinProbability:
    """
    Predicts win probability using Logistic Regression based on historical features.
    Features might include: [Home_Rating, Away_Rating, Home_Rest, Away_Rest]
    """
    def __init__(self):
        self.model = LogisticRegression()
        self.is_trained = False

    def train(self, X, y):
        """
        X: 2D array of features
        y: 1D array of outcomes (1 for win, 0 for loss)
        """
        try:
            self.model.fit(X, y)
            self.is_trained = True
            logger.info("Logistic model trained successfully.")
        except Exception as e:
            logger.error(f"Failed to train logistic model: {e}")

    def predict_prob(self, features):
        """
        Returns the probability of outcome 1 (Home Win).
        """
        if not self.is_trained:
            # Fallback if not trained (e.g., 50/50)
            return 0.5
        
        # model.predict_proba returns [[prob_0, prob_1]]
        return self.model.predict_proba([features])[0][1]

class RatingOptimizer:
    """
    Uses Gradient Descent to optimize parameters of a rating system (Elo-style).
    Optimizes: [K-Factor, Home-Field Advantage]
    """
    def __init__(self, historical_results):
        self.results = historical_results # List of (rating_a, rating_b, outcome)

    def _log_loss(self, params):
        k_factor, hfa = params
        total_loss = 0
        for r_a, r_b, outcome in self.results:
            # Expected win probability for A
            # Outcome 1 = A wins, 0 = B wins
            r_a_adj = r_a + hfa
            p_a = 1 / (1 + 10 ** ((r_b - r_a_adj) / 400))
            
            # Log Loss calculation
            # Avoid log(0)
            p_a = max(min(p_a, 0.999), 0.001)
            loss = -(outcome * np.log(p_a) + (1 - outcome) * np.log(1 - p_a))
            total_loss += loss
        return total_loss / len(self.results)

    def optimize(self):
        initial_params = [20.0, 50.0] # [K, HFA]
        res = minimize(self._log_loss, initial_params, method='L-BFGS-B', bounds=[(10, 100), (0, 200)])
        logger.info(f"Optimized Parameters: K={res.x[0]:.2f}, HFA={res.x[1]:.2f}")
        return res.x

class MonteCarloPricer:
    """
    Simulates outcomes to price complex markets (Spreads, Totals).
    """
    def __init__(self, iterations=10000):
        self.iterations = iterations

    def simulate_game(self, mean_a, mean_b, std_dev=10):
        """
        Simulates game scores and returns fair probabilities for various markets.
        """
        scores_a = np.random.normal(mean_a, std_dev, self.iterations)
        scores_b = np.random.normal(mean_b, std_dev, self.iterations)
        
        # 1. Moneyline
        ml_a_prob = np.mean(scores_a > scores_b)
        
        # 2. Point Spread (e.g., A -3.5)
        spread = 3.5
        spread_a_prob = np.mean((scores_a - spread) > scores_b)
        
        # 3. Over/Under (e.g., Total 210.5)
        total_line = 210.5
        over_prob = np.mean((scores_a + scores_b) > total_line)
        
        return {
            "ml_prob": float(ml_a_prob),
            "spread_prob": float(spread_a_prob),
            "over_prob": float(over_prob)
        }

if __name__ == "__main__":
    # Quick Validation
    print("Testing Monte Carlo...")
    mc = MonteCarloPricer(iterations=1000)
    print(mc.simulate_game(110, 105))
    
    print("\nTesting Rating Optimizer...")
    dummy_results = [(1500, 1500, 1), (1500, 1550, 0), (1600, 1500, 1)]
    ro = RatingOptimizer(dummy_results)
    print(f"Optimal Params: {ro.optimize()}")
