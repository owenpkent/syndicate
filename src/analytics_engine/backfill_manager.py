import os
import subprocess
import time

def run_backfill(sport_id, start, end):
    print(f"--- Backfilling Sport ID {sport_id}: {start} to {end} ---")
    cmd = [
        "python", "historical_scraper.py",
        "--start-date", start,
        "--end-date", end,
        "--sport-id", str(sport_id)
    ]
    subprocess.run(cmd)

if __name__ == "__main__":
    # Sports: NBA=4, NFL=2, MLB=1, NHL=6
    slates = [
        (4, "2023-10-24", "2024-04-14"), # NBA
        (2, "2023-09-07", "2024-01-07"), # NFL
        (1, "2024-03-20", "2024-09-29"), # MLB
        (6, "2023-10-10", "2024-04-18")  # NHL
    ]
    
    for sport_id, start, end in slates:
        run_backfill(sport_id, start, end)
        print("Cooling down between slates...")
        time.sleep(120) # 2 minute cool down between sports
