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
