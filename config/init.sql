-- Sportsball schema (v0.2, normalized).
--
-- One row per game in `events`; `signals` and `trades` reference it by foreign
-- key. This replaces the original string-coupled design where event/team
-- identity was recovered with `market_id LIKE '%' || event_id || '%'` joins
-- (fragile + O(n^2)). See docs/SCHEMA.md for the full data model.
--
-- NOTE: Postgres only runs this file on an empty data directory. After a schema
-- change, reset with `docker compose down -v && docker compose up -d` (this
-- wipes the demo data volume) or apply a migration by hand.

-- ---------------------------------------------------------------------------
-- events: the single source of truth for a game. Created as a SCHEDULED stub
-- when the Engine first sees a market, then filled with scores + closing lines
-- by the backfill / results feed (status -> FINAL).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    sport_id    INTEGER,
    event_date  TIMESTAMPTZ,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'SCHEDULED',  -- SCHEDULED | FINAL
    home_score  INTEGER,
    away_score  INTEGER,
    home_close  NUMERIC(10, 4),   -- closing decimal odds (for CLV)
    away_close  NUMERIC(10, 4),
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- signals: every modeled evaluation the Engine logged (whether or not traded).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id         BIGSERIAL PRIMARY KEY,
    event_id   TEXT NOT NULL REFERENCES events(event_id),
    side       TEXT NOT NULL,                 -- HOME | AWAY
    source     TEXT,
    ts         TIMESTAMPTZ DEFAULT now(),
    odds       NUMERIC(10, 4),
    true_prob  NUMERIC(10, 4),
    ev         NUMERIC(10, 4)
);

-- ---------------------------------------------------------------------------
-- trades: executed paper positions and their settled outcome.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id             BIGSERIAL PRIMARY KEY,
    event_id       TEXT NOT NULL REFERENCES events(event_id),
    side           TEXT NOT NULL,             -- HOME | AWAY
    market_id      TEXT,                      -- venue identifier (exposure-hash key); NOT used for joins
    source         TEXT,
    executed_ts    TIMESTAMPTZ DEFAULT now(),
    executed_odds  NUMERIC(10, 4),
    stake_frac     NUMERIC(10, 4),
    status         TEXT NOT NULL,             -- OPEN | WIN | LOSS | FAILED | ARB_LEG
    is_arb         BOOLEAN DEFAULT FALSE,
    pnl            NUMERIC(12, 6),            -- in stake (fraction) units, set at settlement
    settled_ts     TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- team_advanced_stats: efficiency metrics for Engine enrichment.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_advanced_stats (
    team_name    TEXT PRIMARY KEY,
    off_rating   NUMERIC(10, 2),
    def_rating   NUMERIC(10, 2),
    net_rating   NUMERIC(10, 2),
    pace         NUMERIC(10, 2),
    ts_pct       NUMERIC(10, 4),
    last_updated TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_status ON events (status);
CREATE INDEX IF NOT EXISTS idx_events_date ON events (event_date);
CREATE INDEX IF NOT EXISTS idx_signals_event ON signals (event_id);
CREATE INDEX IF NOT EXISTS idx_trades_event ON trades (event_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_executed_ts ON trades (executed_ts DESC);
