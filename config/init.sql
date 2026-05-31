CREATE TABLE IF NOT EXISTS market_history (
    id SERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    event_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    odds NUMERIC(10, 4),
    true_prob NUMERIC(10, 4),
    ev NUMERIC(10, 4)
);

CREATE TABLE IF NOT EXISTS trade_history (
    id SERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    executed_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    executed_odds NUMERIC(10, 4),
    fraction NUMERIC(10, 4),
    status TEXT
);

CREATE TABLE IF NOT EXISTS historical_results (
    event_id TEXT PRIMARY KEY,
    sport_id INTEGER,
    event_date TIMESTAMP,
    home_team TEXT,
    away_team TEXT,
    home_score INTEGER,
    away_score INTEGER,
    home_odds NUMERIC(10, 4),
    away_odds NUMERIC(10, 4)
);

-- Advanced team efficiency metrics used by the Analytics Engine to enrich
-- Elo ratings (see get_team_stats / Net Rating Adjustment in docs/QUANT.md).
-- Populated by scripts/fetch_nba_stats.py; created here so the engine's
-- enrichment query never hits a missing relation on a cold database.
CREATE TABLE IF NOT EXISTS team_advanced_stats (
    team_name TEXT PRIMARY KEY,
    off_rating NUMERIC(10, 2),
    def_rating NUMERIC(10, 2),
    net_rating NUMERIC(10, 2),
    pace NUMERIC(10, 2),
    ts_pct NUMERIC(10, 4),
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for the hot lookup paths: signal logging, settlement joins,
-- and dashboard/CLV queries that filter by status or scan recent trades.
CREATE INDEX IF NOT EXISTS idx_market_history_market_id ON market_history (market_id);
CREATE INDEX IF NOT EXISTS idx_trade_history_status ON trade_history (status);
CREATE INDEX IF NOT EXISTS idx_trade_history_executed_ts ON trade_history (executed_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_historical_results_event_date ON historical_results (event_date);
