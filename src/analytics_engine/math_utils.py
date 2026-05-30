import numpy as np
from scipy.stats import poisson

def calculate_ev(true_prob, odds):
    """
    Expected Value (EV) Calculation
    EV = (P_true * O) - 1
    """
    return (true_prob * odds) - 1

def calculate_kelly_fraction(ev, odds, multiplier=0.25):
    """
    Fractional Kelly Criterion
    f* = EV / (O - 1)
    f_actual = multiplier * f*
    """
    if odds <= 1:
        return 0
    
    f_star = ev / (odds - 1)
    return multiplier * max(0, f_star)

def poisson_probability(k, lam):
    """
    Poisson Distribution: P(X=k) = (lambda^k * e^-lambda) / k!
    """
    return poisson.pmf(k, lam)

def calculate_joint_poisson_prob(lam_a, lam_b, max_k=50):
    """
    Generates a joint probability matrix for two independent Poisson processes.
    Used for point spreads and totals.
    """
    prob_a = poisson.pmf(np.arange(max_k), lam_a)
    prob_b = poisson.pmf(np.arange(max_k), lam_b)
    return np.outer(prob_a, prob_b)
