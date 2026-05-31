import psycopg2
import os
import matplotlib.pyplot as plt
import numpy as np

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database="market_history",
        user="sportsball_admin",
        password="changeme_in_env"
    )

def plot_calibration():
    """
    Plots Predicted vs Actual win rate to check if our 'true_prob' is accurate.
    """
    print("Generating Calibration Plot...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Join market_history with historical_results to see how we did
            # This requires matching the market_id (e.g. RUNDOWN-EVENTID-TEAM) 
            # to the event_id and outcome in historical_results.
            cur.execute("""
                SELECT mh.true_prob, 
                       CASE WHEN hr.home_score > hr.away_score THEN 1 ELSE 0 END as actual_home,
                       hr.home_team
                FROM market_history mh
                JOIN historical_results hr ON mh.market_id LIKE '%' || hr.event_id || '%'
                WHERE hr.home_score IS NOT NULL
            """)
            data = cur.fetchall()
            
            if not data:
                print("Not enough matched history to generate calibration.")
                return

            probs = np.array([float(d[0]) for d in data])
            actuals = np.array([d[1] for d in data])

            # Bin the probabilities (0-0.1, 0.1-0.2, etc)
            bins = np.linspace(0, 1, 11)
            bin_indices = np.digitize(probs, bins) - 1
            
            bin_means = []
            bin_actuals = []
            
            for i in range(len(bins) - 1):
                mask = (bin_indices == i)
                if np.any(mask):
                    bin_means.append(np.mean(probs[mask]))
                    bin_actuals.append(np.mean(actuals[mask]))

            plt.figure(figsize=(8, 8))
            plt.plot([0, 1], [0, 1], 'k--', label="Perfect Calibration")
            plt.plot(bin_means, bin_actuals, 's-', label="Sportsball Model")
            plt.title("Model Calibration: Predicted vs Actual Win Rate")
            plt.xlabel("Predicted Win Probability")
            plt.ylabel("Actual Win Rate")
            plt.legend()
            plt.grid(True)
            
            output_path = "calibration_plot.png"
            plt.savefig(output_path)
            print(f"Calibration plot saved to {output_path}")

    finally:
        conn.close()

if __name__ == "__main__":
    plot_calibration()
