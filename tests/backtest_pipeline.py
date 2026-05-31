import json
import argparse
import sys
import os

# Add /app to path to reuse math logic
sys.path.append('/app')

from math_utils import calculate_ev, calculate_kelly_fraction

def run_backtest(data_file, initial_bankroll, ev_buffer, kelly_multiplier):
    print(f"--- Starting Backtest ---")
    print(f"Initial Bankroll: ${initial_bankroll:.2f}")
    print(f"EV Buffer: {ev_buffer} | Kelly Multiplier: {kelly_multiplier}")
    print("-" * 30)

    try:
        with open(data_file, 'r') as f:
            ticks = json.load(f)
    except FileNotFoundError:
        print(f"Error: {data_file} not found.")
        return

    bankroll = initial_bankroll
    trades_executed = 0
    total_ev = 0
    wins = 0
    losses = 0

    for tick in ticks:
        true_prob = tick.get("true_prob")
        odds = tick.get("odds")
        market_id = tick.get("market_id", tick.get("event", "Unknown"))
        
        # In backtesting, we also need the actual outcome to calculate PnL
        # If outcome isn't in mock data, we simulate it based on true_prob
        outcome = tick.get("outcome") 
        if outcome is None:
            import random
            outcome = 1 if random.random() < true_prob else 0

        ev = calculate_ev(true_prob, odds)
        
        if ev > ev_buffer:
            fraction = calculate_kelly_fraction(ev, odds, kelly_multiplier)
            risk_amount = bankroll * fraction
            
            if risk_amount > 0:
                trades_executed += 1
                total_ev += ev
                
                if outcome == 1:
                    profit = risk_amount * (odds - 1)
                    bankroll += profit
                    wins += 1
                    status = "WIN"
                else:
                    bankroll -= risk_amount
                    losses += 1
                    status = "LOSS"
                
                print(f"[TRADE] {market_id:<20} | Odds: {odds:.2f} | Risk: ${risk_amount:>7.2f} | {status} | Bankroll: ${bankroll:>8.2f}")

    print("-" * 30)
    print(f"--- Backtest Results ---")
    print(f"Total Trades:      {trades_executed}")
    print(f"Win Rate:         {(wins/trades_executed*100 if trades_executed > 0 else 0):.2f}%")
    print(f"Final Bankroll:    ${bankroll:.2f}")
    print(f"Total Profit/Loss: ${(bankroll - initial_bankroll):.2f}")
    print(f"ROI:              {((bankroll/initial_bankroll - 1) * 100):.2f}%")
    print(f"Average EV:       {(total_ev/trades_executed if trades_executed > 0 else 0):.4f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sportsball Backtesting Engine")
    parser.add_argument("--input", default="tests/mock_ticks.json", help="Path to tick data")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Starting capital")
    parser.add_argument("--buffer", type=float, default=0.02, help="EV safety buffer")
    parser.add_argument("--kelly", type=float, default=0.25, help="Fractional Kelly multiplier")
    
    args = parser.parse_args()
    
    # Check if file exists relative to execution point
    input_path = args.input
    if not os.path.exists(input_path) and os.path.exists(os.path.join("sportsball", input_path)):
        input_path = os.path.join("sportsball", input_path)

    run_backtest(input_path, args.bankroll, args.buffer, args.kelly)
