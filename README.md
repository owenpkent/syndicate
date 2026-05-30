# Autonomous Sports Analytics Syndicate (Project: Syndicate)

An autonomous, distributed-agent quantitative trading pipeline and validation environment optimized for high-performance headless Mini PCs (specifically configured for AMD Ryzen 9 5900HX, 32GB RAM, Ubuntu 24.04). 

This platform orchestrates an ensemble of specialized micro-agents that ingest sports data, calculate real-time expected value, optimize bankroll sizing, and interface with decentralized prediction market protocols natively and anonymously.

---

## ─── Architecture Overview ───

The architecture executes a "Cluster in a Box" design pattern using Docker containers to isolate specialized agent roles. This ensures multi-threaded efficiency across CPU cores, zero dependency cross-contamination, and isolated execution logic.

```
              ┌───────────────── [ Oracle Agent ] (Real-World Odds API)
              │
              ▼
[ Redis Stream / Message Broker ] ◄─► [ Analytics Engine ] ──► [ DB / Log Layer ]
▲                             │
│                             ▼
└───────────────── [ Scout Agent ] (Polymarket WebSockets)
│
▼
[ Sniper Agent ] (Executioner)
```

### Micro-Agent Specifications
* **The Oracle Agent (Data Ingestion):** Continuous polling loop fetching live market lines, point spreads, and contract data from global sharp bookmakers.
* **The Scout Agent (Market Watcher):** Maintains an open, low-latency WebSocket connection to decentralized order books to track active liquidity pool pricing changes.
* **The Analytics Engine (The Brain):** Subscribes to the data streams, processes high-frequency matrix calculations, and flags discrepancies where the true probability exceeds market pricing.
* **The Sniper Agent (Executioner):** A highly secured, isolated runtime container containing encrypted programmatic Web3 keys to sign transactions or log simulated paper trades.

---

## ─── Project Directory Structure ───

Initialize the root directory of the repository using the exact tree specification below:

```text
.
├── .env.example              # Template for keys, RPC endpoints, and private variables
├── README.md                 # System documentation and mathematical specification
├── docker-compose.yml        # Multi-container orchestration manifest
├── config/                   # Global configuration parameters
│   └── settings.json
├── data/                     # Local persistent data volumes (git ignored)
│   ├── postgres/
│   └── redis/
├── src/                      # Micro-agent implementations
│   ├── analytics_engine/     # Python engine for mathematical modeling and distributions
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── oracle_agent/         # Scrapers and sharp market consumers
│   │   └── main.py
│   ├── scout_agent/          # Blockchain socket listeners
│   │   └── main.py
│   └── sniper_agent/         # Order execution logic and paper trading logs
│       └── main.py
└── tests/                    # Simulation and algorithmic validation framework
    ├── backtest_pipeline.py
    └── mock_ticks.json
```

---

## ─── Infrastructure Manifest ───

### `docker-compose.yml`

Save this configuration exactly to the root directory to manage the container lifecycle across the 16 execution threads of the system processor:

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    container_name: syndicate_broker
    command: redis-server --appendonly yes
    ports:
      - "6379:6379"
    volumes:
      - ./data/redis:/data
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    container_name: syndicate_db
    environment:
      POSTGRES_USER: syndicate_admin
      POSTGRES_PASSWORD: changeme_in_env
      POSTGRES_DB: market_history
    ports:
      - "5432:5432"
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    restart: unless-stopped

  oracle_agent:
    build: ./src/oracle_agent
    container_name: agent_oracle
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
    restart: always

  scout_agent:
    build: ./src/scout_agent
    container_name: agent_scout
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
    restart: always

  analytics_engine:
    build: ./src/analytics_engine
    container_name: agent_engine
    depends_on:
      - redis
      - postgres
    environment:
      - REDIS_HOST=redis
      - DB_HOST=postgres
    restart: always

  sniper_agent:
    build: ./src/sniper_agent
    container_name: agent_sniper
    depends_on:
      - redis
    env_file:
      - .env
    environment:
      - REDIS_HOST=redis
    restart: always
```

---

## ─── Quantitative Mathematical Pipeline ───

The `analytics_engine` evaluates all inputs via a three-tiered mathematical validation framework before routing any execution signals to the broker.

### 1. Expected Value ($EV$) Calculation

The market price of a binary contract or line represents an implied probability. For decimal odds $O$, the market's implied probability $P_{\text{market}}$ is defined as:

$P_{\text{market}}=\frac{1}{O}$

The system evaluates a prospective position by finding a positive expected value ($EV>0$). The net return per unit of capital staked is calculated using the model's computed true probability ($P_{\text{true}}$):

$$EV=(P_{\text{true}}\times O)-1$$

Positions are rejected automatically by the processing loop if the generated $EV$ falls below a strict safety buffer (e.g., $EV<0.02$).

### 2. Sizing Optimization (Fractional Kelly Criterion)

To achieve maximum long-term logarithmic asset growth while protecting against catastrophic variance and drawdowns, the capital allocation fraction ($f^*$) is determined dynamically:

$$f^*=\frac{EV}{O-1}$$

To mitigate risk from parameter estimation error, the `sniper_agent` enforces a strict Fractional Kelly multiplier ($c$). The actual percentage of the bankroll risked per trade is calculated as:

$f_{\text{actual}}=c\times f^* \quad \text{where } 0<c<1$

*Standard Production Safeguard:* The system enforces a Quarter-Kelly constraint ($c=0.25$) within the runtime configuration to smooth the variance curve.

### 3. Generative Scoring Distributions (Poisson Modeling)

For discrete, independent scoring environments (such as team point totals, soccer match outcomes, or player statistical performance metrics), lines are generated using a Poisson Distribution.

The probability that an asset scores exactly $k$ points given an expected performance baseline $\lambda$ (derived from historical offensive and defensive efficiency matrices) is computed by:

$$P(X=k)=\frac{\lambda^k e^{-\lambda}}{k!}$$

Where $e$ is Euler's number ($\approx 2.71828$) and $k!$ is the factorial of $k$. The engine aggregates individual scoring distributions into a multi-dimensional joint matrix to compute exact probabilities for over/under boundaries and point spreads.

---

## ─── Validation & Verification Framework ───

To protect capital from algorithmic failure, strict software-isolated validation protocols must be executed prior to production environment access.

### 1. Backtesting Protocol

Execute historical out-of-sample evaluations using tick data arrays. The validation data must be completely separate from data sets used to build the predictive models to avoid overfitting:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r src/analytics_engine/requirements.txt
python3 tests/backtest_pipeline.py --input tests/mock_ticks.json
```

### 2. Forward Paper Testing (Simulated Execution)

Before processing live blockchain network transactions, the system must be run in a live headless environment using paper execution tracking. This verifies edge stability, network latency buffers, and slippage calculations without financial exposure.

Verify your `.env` contains the following structural configurations:

```env
# Verification Controls
EXECUTION_MODE=PAPER
SLIPPAGE_TOLERANCE_PCT=0.005

# Network Parameters
WALLET_PRIVATE_KEY=mock_key_for_simulation_purposes_only
POLYMARKET_API_KEY=read_only_public_tracking_key
```

To trail and evaluate live paper performance, monitor container stdout logs using Docker:

```bash
docker compose logs -f analytics_engine sniper_agent
```

---

## ─── Deployment Instructions ───

1. Clone this repository directly onto your local Ubuntu server.
2. Initialize environment dependencies: `cp .env.example .env`
3. Spin up the cluster infrastructure: `docker compose up -d --build`
4. Confirm database and broker status parameters: `docker compose ps`
