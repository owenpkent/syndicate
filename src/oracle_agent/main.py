import time
import os
import redis

def main():
    print("Oracle Agent starting...")
    redis_host = os.getenv("REDIS_HOST", "localhost")
    r = redis.Redis(host=redis_host, port=6379, db=0)
    
    while True:
        # Placeholder for polling live market lines
        print("Oracle Agent: Polling market data...")
        time.sleep(60)

if __name__ == "__main__":
    main()
