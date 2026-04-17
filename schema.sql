-- ============================================
-- Cricket Odds Tracker - Supabase Schema
-- Run this in Supabase SQL Editor
-- ============================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

-- ============================================
-- EVENTS TABLE
-- Stores scraping targets (matches/events)
-- ============================================
CREATE TABLE events (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    event_type TEXT,                    -- 'test', 'odi', 't20', 'other'
    odds_url TEXT,                      -- Sportsbet URL
    score_url TEXT,                     -- ESPN Cricinfo URL
    cricbuzz_url TEXT,                  -- Cricbuzz URL (alternative to score_url)
    score_source TEXT DEFAULT 'cricinfo', -- 'cricinfo' or 'cricbuzz'
    outcomes INTEGER DEFAULT 2,         -- 2 for win/win, 3 for win/draw/win
    allowed_outcomes JSONB,             -- ["Team A", "Team B", "Draw"]
    outcome_colors JSONB,               -- {"Team A": "#ff0000", "Team B": "#00ff00"}
    outcome_order JSONB,                -- ["Team A", "Team B", "Draw"] - display order
    active BOOLEAN DEFAULT true,        -- Currently scraping?
    archived BOOLEAN DEFAULT false,     -- Match finished?
    start_date DATE,
    end_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- SNAPSHOTS TABLE
-- One row per scrape cycle per event
-- ============================================
CREATE TABLE snapshots (
    id SERIAL PRIMARY KEY,
    match_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    match_status TEXT,                  -- "Live", "Stumps", etc.
    match_stage TEXT,
    match_state TEXT,
    errors TEXT                         -- Error messages if scrape failed
);

-- ============================================
-- ODDS TABLE
-- Betting odds per outcome per snapshot
-- ============================================
CREATE TABLE odds (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL,              -- "Australia", "England", "Draw"
    odds REAL NOT NULL,
    implied_probability REAL NOT NULL
);

-- ============================================
-- INNINGS TABLE
-- Cricket innings data per snapshot
-- ============================================
CREATE TABLE innings (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    team TEXT NOT NULL,
    inning_number INTEGER,
    runs INTEGER,
    wickets INTEGER,
    overs REAL
);

-- ============================================
-- REQUEST_LOG TABLE
-- Analytics / visitor tracking
-- ============================================
CREATE TABLE request_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    path TEXT,
    ip TEXT,
    user_agent TEXT,
    request_type TEXT DEFAULT 'page'    -- 'page' or 'refresh'
);

-- ============================================
-- KNOWN_IPS TABLE
-- Filter out known IPs from visitor stats
-- ============================================
CREATE TABLE known_ips (
    ip TEXT PRIMARY KEY,
    owner TEXT
);

-- ============================================
-- IP_LOCATIONS TABLE
-- Cached IP geolocation data
-- ============================================
CREATE TABLE ip_locations (
    ip TEXT PRIMARY KEY,
    country TEXT,
    country_code TEXT,
    city TEXT,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- INDEXES
-- ============================================
CREATE INDEX idx_snapshots_match_id ON snapshots(match_id);
CREATE INDEX idx_snapshots_timestamp ON snapshots(timestamp DESC);
CREATE INDEX idx_odds_snapshot_id ON odds(snapshot_id);
CREATE INDEX idx_innings_snapshot_id ON innings(snapshot_id);
CREATE INDEX idx_request_log_timestamp ON request_log(timestamp DESC);
CREATE INDEX idx_request_log_ip ON request_log(ip);

-- ============================================
-- DISABLE ROW LEVEL SECURITY
-- (Using server-side connections only)
-- ============================================
ALTER TABLE events DISABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots DISABLE ROW LEVEL SECURITY;
ALTER TABLE odds DISABLE ROW LEVEL SECURITY;
ALTER TABLE innings DISABLE ROW LEVEL SECURITY;
ALTER TABLE request_log DISABLE ROW LEVEL SECURITY;
ALTER TABLE known_ips DISABLE ROW LEVEL SECURITY;
ALTER TABLE ip_locations DISABLE ROW LEVEL SECURITY;
