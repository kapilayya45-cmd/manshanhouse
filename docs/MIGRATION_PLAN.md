# Migration Plan: SQLite to Supabase + Render

## Overview

This document outlines the complete migration of the Cricket Odds Tracker from a local SQLite-based setup to a cloud-hosted architecture using Supabase (Postgres) and Render.

### Current Architecture
```
Local Machine
├── app.py (Flask web server)
├── monitor.py (continuous scraper process)
├── match_data.db (SQLite database)
└── cloudflared (tunnel for public access)
```

### Target Architecture
```
┌─────────────────┐         ┌──────────────────┐
│    Supabase     │         │      Render      │
│   (Postgres)    │◄───────►│  (Flask + API)   │
│                 │         │                  │
│  pg_cron ───────┼── HTTP ─┼─► /api/scrape    │
└─────────────────┘         └──────────────────┘
        │                           │
        └───────────┬───────────────┘
                    ▼
              Public Users
```

---

## Prerequisites & Manual Setup

These are the non-coding tasks that require manual action in web UIs.

### 1. GitHub Repository

**If not already done:**

1. Go to https://github.com/new
2. Create a new repository (e.g., `cricket-odds-tracker`)
3. Set visibility (public or private)
4. Don't initialize with README (we have existing code)
5. Copy the remote URL

**In your local project:**
```bash
git remote add origin https://github.com/YOUR_USERNAME/cricket-odds-tracker.git
git push -u origin main
```

### 2. Supabase Account & Project

**Create account:**

1. Go to https://supabase.com
2. Click "Start your project"
3. Sign up with GitHub (recommended) or email
4. Verify email if required

**Create project:**

1. Click "New Project"
2. Select your organization (or create one - free)
3. Fill in:
   - **Name**: `cricket-odds-tracker`
   - **Database Password**: Generate a strong password and **save it somewhere secure**
   - **Region**: Choose closest to your users (e.g., `Sydney` for Australia)
4. Click "Create new project"
5. Wait 1-2 minutes for provisioning

**Get connection string:**

1. Go to Project Settings (gear icon) → Database
2. Scroll to "Connection string"
3. Select "URI" tab
4. Copy the "Transaction pooler" connection string (port 6543)
5. Replace `[YOUR-PASSWORD]` with your database password

**Connection string format:**
```
postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
```

**Enable extensions:**

1. Go to SQL Editor (left sidebar)
2. Click "New query"
3. Run:
```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;
```
4. Click "Run" (or Cmd/Ctrl+Enter)

### 3. Render Account & Service

**Create account:**

1. Go to https://render.com
2. Click "Get Started"
3. Sign up with GitHub (recommended - enables auto-deploy)
4. Authorize Render to access your repositories

**Create web service:**

1. Click "New" → "Web Service"
2. Connect your GitHub repository
3. Configure:
   - **Name**: `cricket-odds-tracker`
   - **Region**: Choose closest to Supabase region
   - **Branch**: `main`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: `Free`
4. Click "Create Web Service"

**Set environment variables:**

1. Go to your service → "Environment" tab
2. Add each variable:

| Key | Value | How to get it |
|-----|-------|---------------|
| `DATABASE_URL` | `postgresql://postgres...` | Supabase connection string (see above) |
| `SECRET_KEY` | Random 32+ character string | Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ADMIN_PASSWORD` | Your choice | Password for `/admin` login |
| `SCRAPE_TOKEN` | Random 32+ character string | Generate same as SECRET_KEY |

3. Click "Save Changes" (triggers redeploy)

### 4. Supabase Cron Job

**After Render is deployed and you have your URL:**

1. Go to Supabase → SQL Editor
2. Run (replace YOUR values):

```sql
SELECT cron.schedule(
    'scrape-every-minute',
    '* * * * *',
    $$
    SELECT net.http_post(
        url := 'https://YOUR-APP.onrender.com/api/scrape',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer YOUR_SCRAPE_TOKEN'
        ),
        timeout_milliseconds := 25000
    );
    $$
);
```

**Verify cron is running:**
```sql
-- Check job exists
SELECT * FROM cron.job;

-- Check recent runs (wait a few minutes first)
SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 10;
```

### 5. Domain Setup (Optional)

If you want a custom domain instead of `*.onrender.com`:

1. In Render: Settings → Custom Domain → Add your domain
2. Update DNS records as instructed (CNAME to `*.onrender.com`)
3. Wait for SSL certificate (automatic, ~15 mins)
4. Update Supabase cron job with new URL

---

## Design Decisions & Rationale

### Why Supabase?

1. **Free tier** - 500MB database, unlimited API requests
2. **Postgres** - Industry standard, robust, full SQL support
3. **pg_cron + pg_net** - Built-in cron that can make HTTP requests (replaces need for background worker)
4. **Good DX** - Web UI for SQL editor, table viewer, easy setup

**Alternatives considered:**
- Neon (Postgres): Similar, but pg_cron requires paid tier
- Turso (SQLite): Would minimize code changes, but less mature
- PlanetScale (MySQL): Removed free tier in 2024

### Why Render?

1. **Free tier** - 750 hours/month web service (enough for one always-on service)
2. **Simple deployment** - Git push to deploy
3. **Environment variables** - Easy secrets management

**Alternatives considered:**
- Vercel: Better for serverless, Flask support is awkward
- Fly.io: Good but more complex setup
- Railway: Free tier limited to $5/month credits

### Why HTTP-triggered scraping instead of background worker?

1. **Cost** - Render background workers are paid only
2. **Simplicity** - Single service to manage
3. **Reliability** - Supabase cron is managed, no process to monitor
4. **Flexibility** - Can manually trigger scrapes via API

**Trade-offs:**
- 30-second request timeout on Render free tier (scraping 3-5 events should complete in ~10s)
- Cold starts after 15min inactivity (first scrape after sleep may timeout)

---

## Implementation Phases

### Phase 1: Dependencies & Project Setup

#### 1.1 Create `requirements.txt`

```
flask==3.0.0
gunicorn==21.2.0
psycopg2-binary==2.9.9
requests==2.31.0
beautifulsoup4==4.12.2
```

**Rationale:**
- `gunicorn`: Production WSGI server (Flask's dev server is not production-ready)
- `psycopg2-binary`: Postgres driver. Using `-binary` to avoid compilation issues on Render

#### 1.2 Create Supabase Project

1. Sign up at https://supabase.com
2. Create new project (free tier)
3. Note the project URL and anon key
4. Get connection string: Settings → Database → Connection string → URI

**Connection string format:**
```
postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres
```

**Important:** Use the "Transaction pooler" connection string (port 6543) for serverless environments like Render. The direct connection (port 5432) may hit connection limits.

---

### Phase 2: Database Schema

#### 2.1 Schema Design

Key changes from SQLite:
- `SERIAL` instead of `INTEGER PRIMARY KEY AUTOINCREMENT`
- `TIMESTAMPTZ` instead of `TEXT` for timestamps
- `JSONB` instead of `TEXT` for `allowed_outcomes` (enables querying)
- `BOOLEAN` instead of `INTEGER` for flags
- Explicit foreign key constraints

#### 2.2 Create Tables

Run in Supabase SQL Editor:

```sql
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
    outcomes INTEGER DEFAULT 2,         -- 2 for win/win, 3 for win/draw/win
    allowed_outcomes JSONB,             -- ["Team A", "Team B", "Draw"]
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
-- COMMENTARY TABLE (optional)
-- Ball-by-ball commentary
-- ============================================
CREATE TABLE commentary (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    over_number REAL,
    title TEXT,
    text TEXT,
    is_wicket BOOLEAN DEFAULT false,
    runs INTEGER
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
-- INDEXES
-- ============================================
CREATE INDEX idx_snapshots_match_id ON snapshots(match_id);
CREATE INDEX idx_snapshots_timestamp ON snapshots(timestamp DESC);
CREATE INDEX idx_odds_snapshot_id ON odds(snapshot_id);
CREATE INDEX idx_innings_snapshot_id ON innings(snapshot_id);
CREATE INDEX idx_request_log_timestamp ON request_log(timestamp DESC);
CREATE INDEX idx_request_log_ip ON request_log(ip);
```

#### 2.3 Row Level Security (Optional but Recommended)

Supabase enables RLS by default. For this app, we'll disable it since we're using server-side connections:

```sql
ALTER TABLE events DISABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots DISABLE ROW LEVEL SECURITY;
ALTER TABLE odds DISABLE ROW LEVEL SECURITY;
ALTER TABLE innings DISABLE ROW LEVEL SECURITY;
ALTER TABLE commentary DISABLE ROW LEVEL SECURITY;
ALTER TABLE request_log DISABLE ROW LEVEL SECURITY;
ALTER TABLE known_ips DISABLE ROW LEVEL SECURITY;
```

**Note:** If you later want to expose Supabase directly to the frontend (bypassing Flask), you'd want to enable RLS with appropriate policies.

---

### Phase 3: Code Changes

#### 3.1 Create `db.py` - Database Connection Module

```python
"""
Database connection module.
Handles Postgres connections for both app.py and monitor.py.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

DATABASE_URL = os.environ.get('DATABASE_URL')

@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def get_cursor(conn):
    """Context manager for database cursors with dict results."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()
```

**Rationale:**
- Context managers ensure connections are always closed (prevents connection leaks)
- `RealDictCursor` returns rows as dicts (matches SQLite's `row_factory = sqlite3.Row` behavior)
- Centralized connection logic for consistency

#### 3.2 Key Code Changes in `app.py`

**SQLite → Postgres syntax changes:**

| SQLite | Postgres |
|--------|----------|
| `?` | `%s` |
| `datetime('now')` | `NOW()` |
| `date('now')` | `CURRENT_DATE` |
| `datetime('now', '-3 hours')` | `NOW() - INTERVAL '3 hours'` |
| `sqlite3.Row` | `RealDictCursor` |
| `c.lastrowid` | `RETURNING id` clause |

**Example conversion:**

```python
# Before (SQLite)
c.execute('INSERT INTO events (name) VALUES (?)', (name,))
event_id = c.lastrowid

# After (Postgres)
c.execute('INSERT INTO events (name) VALUES (%s) RETURNING id', (name,))
event_id = c.fetchone()['id']
```

#### 3.3 Add `/api/scrape` Endpoint

```python
@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    """
    Trigger a scrape cycle. Protected by token authentication.
    Called by Supabase pg_cron every minute.
    """
    # Check authorization
    token = request.args.get('token')
    if not token:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

    expected_token = os.environ.get('SCRAPE_TOKEN')
    if not expected_token or token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401

    # Run scrape
    try:
        from scraper import run_once
        results = run_once()
        return jsonify({
            'success': True,
            'events_scraped': len(results),
            'results': results
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
```

**Security considerations:**
- Token-based auth prevents unauthorized scrape triggers
- Token passed via query param (for pg_net compatibility) or Authorization header
- Returns structured response for debugging

#### 3.4 Refactor `monitor.py` → `scraper.py`

Rename and refactor to be importable:

```python
def run_once():
    """Run a single scrape cycle. Returns results dict."""
    results = []
    events = get_active_events()

    for event in events:
        result = scrape_event(event)
        results.append(result)

    return results
```

**Rationale:**
- Separates scraping logic from CLI interface
- Makes it importable by `app.py`
- `if __name__ == '__main__'` block still allows standalone execution for local testing

---

### Phase 4: Data Migration

#### 4.1 Export Strategy

For a small dataset, manual migration is fine. For larger datasets:

```bash
# Export events
sqlite3 -header -csv match_data.db "SELECT * FROM events" > events.csv

# Export snapshots (may be large)
sqlite3 -header -csv match_data.db "SELECT * FROM snapshots" > snapshots.csv

# etc.
```

#### 4.2 Import to Supabase

Option A: **Supabase CSV import** (Web UI)
- Table Editor → Import CSV
- Works well for small tables

Option B: **SQL INSERT statements**
- Export as SQL: `sqlite3 match_data.db ".dump events"`
- Convert syntax (see conversion table above)
- Run in SQL Editor

Option C: **Python migration script**
```python
# migrate.py - one-time migration script
import sqlite3
import psycopg2

# Connect to both databases
sqlite_conn = sqlite3.connect('match_data.db')
pg_conn = psycopg2.connect(DATABASE_URL)

# Migrate events
for row in sqlite_conn.execute('SELECT * FROM events'):
    pg_conn.execute('INSERT INTO events (...) VALUES (...)', row)

pg_conn.commit()
```

#### 4.3 Data Considerations

- **Timestamps**: SQLite stores as TEXT (ISO format), Postgres expects TIMESTAMPTZ
- **Booleans**: SQLite uses 0/1, Postgres uses true/false
- **JSON**: SQLite stores as TEXT string, Postgres JSONB needs parsing

**Migration SQL example:**
```sql
-- If importing timestamps as text, cast them:
INSERT INTO snapshots (match_id, timestamp, ...)
SELECT match_id, timestamp::timestamptz, ...
FROM temp_import;
```

---

### Phase 5: Supabase Cron Setup

#### 5.1 Enable Required Extensions

```sql
-- Enable pg_cron (for scheduling)
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Enable pg_net (for HTTP requests)
CREATE EXTENSION IF NOT EXISTS pg_net;
```

**Note:** These extensions are available on Supabase free tier.

#### 5.2 Create Cron Job

```sql
-- Schedule scrape every minute
SELECT cron.schedule(
    'scrape-every-minute',           -- job name
    '* * * * *',                     -- cron expression (every minute)
    $$
    SELECT net.http_post(
        url := 'https://your-app.onrender.com/api/scrape',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer YOUR_SCRAPE_TOKEN'
        ),
        timeout_milliseconds := 25000
    );
    $$
);
```

#### 5.3 Managing Cron Jobs

```sql
-- View all cron jobs
SELECT * FROM cron.job;

-- View recent job runs
SELECT * FROM cron.job_run_details ORDER BY start_time DESC LIMIT 20;

-- Disable a job
SELECT cron.unschedule('scrape-every-minute');

-- Update schedule (every 2 minutes instead)
SELECT cron.alter_job(
    job_id := (SELECT jobid FROM cron.job WHERE jobname = 'scrape-every-minute'),
    schedule := '*/2 * * * *'
);
```

---

### Phase 6: Render Deployment

#### 6.1 Prepare Repository

Ensure these files exist:
```
├── app.py
├── scraper.py (renamed from monitor.py)
├── db.py
├── requirements.txt
├── templates/
│   ├── index.html
│   ├── admin.html
│   └── login.html
└── .gitignore
```

Update `.gitignore`:
```
venv/
__pycache__/
*.pyc
*.db
.env
```

#### 6.2 Create Render Web Service

1. Go to https://dashboard.render.com
2. New → Web Service
3. Connect GitHub repository
4. Configure:
   - **Name**: cricket-odds-tracker
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: Free

#### 6.3 Environment Variables

Set in Render dashboard (Settings → Environment):

| Variable | Value | Description |
|----------|-------|-------------|
| `DATABASE_URL` | `postgresql://...` | Supabase connection string |
| `SECRET_KEY` | (random 32+ chars) | Flask session encryption |
| `ADMIN_PASSWORD` | (your password) | Admin login |
| `SCRAPE_TOKEN` | (random 32+ chars) | API authentication |

**Generate random tokens:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

#### 6.4 Deploy

```bash
git add -A
git commit -m "Prepare for Render deployment"
git push origin main
```

Render auto-deploys on push to main.

---

### Phase 7: Testing & Verification

#### 7.1 Pre-deployment Testing (Local)

```bash
# Set environment variables
export DATABASE_URL="postgresql://..."
export SECRET_KEY="test-secret"
export ADMIN_PASSWORD="admin"
export SCRAPE_TOKEN="test-token"

# Test database connection
python -c "from db import get_db; print('DB OK')"

# Test Flask app
python app.py

# Test scraper
python scraper.py --once
```

#### 7.2 Post-deployment Testing

1. **Web app loads**: `https://your-app.onrender.com`
2. **Admin login works**: `https://your-app.onrender.com/login`
3. **Events appear**: Check admin panel shows events
4. **Manual scrape**:
   ```bash
   curl -X POST "https://your-app.onrender.com/api/scrape?token=YOUR_TOKEN"
   ```
5. **Cron running**: Check `cron.job_run_details` in Supabase
6. **Data flowing**: New snapshots appearing in database

#### 7.3 Monitoring

**Supabase:**
- Database → Table Editor: View data
- SQL Editor: Run queries
- Logs: Check for errors

**Render:**
- Logs tab: View application logs
- Metrics: CPU/memory usage

---

## Gotchas & Troubleshooting

### Connection Issues

**Problem**: `connection refused` or `timeout`

**Solutions**:
1. Use Transaction Pooler connection string (port 6543), not direct (5432)
2. Check Supabase is not paused (free tier pauses after 1 week inactivity)
3. Verify DATABASE_URL is correctly set in Render

### Cold Starts

**Problem**: First request after 15min inactivity times out

**Solutions**:
1. Supabase cron keeps hitting the endpoint, preventing full sleep
2. Accept that first request after true cold start may fail
3. Consider paid tier ($7/mo) for always-on

### Scrape Timeouts

**Problem**: `/api/scrape` times out (30s limit on free tier)

**Solutions**:
1. Reduce number of active events
2. Add timeout handling per event (skip slow ones)
3. Parallelize scraping (careful with rate limits)

### Supabase Pausing

**Problem**: Free tier pauses after 7 days of inactivity

**Solutions**:
1. The cron job hitting your Render app counts as activity
2. Supabase itself needs database activity - ensure cron is working
3. Log into Supabase dashboard occasionally

### Postgres Syntax Errors

**Problem**: SQL that worked in SQLite fails

**Common fixes**:
```sql
-- String concatenation
SQLite: 'a' || 'b'
Postgres: 'a' || 'b'  -- same, but use CONCAT() for nulls

-- LIMIT with OFFSET
SQLite: LIMIT 10, 5
Postgres: LIMIT 5 OFFSET 10

-- Boolean
SQLite: WHERE active = 1
Postgres: WHERE active = true  -- or just: WHERE active

-- Case sensitivity
SQLite: case-insensitive by default
Postgres: case-sensitive, use ILIKE for insensitive
```

### JSON Handling

**Problem**: `allowed_outcomes` stored differently

**SQLite** (TEXT):
```python
allowed = json.loads(row['allowed_outcomes'])
```

**Postgres** (JSONB):
```python
allowed = row['allowed_outcomes']  # Already parsed!
```

### IP Tracking

**Problem**: All IPs show as same value

**Solution**: Render uses `X-Forwarded-For` header. The existing code handles this:
```python
ip = request.headers.get('X-Forwarded-For', request.remote_addr)
# X-Forwarded-For may contain multiple IPs: "client, proxy1, proxy2"
if ip and ',' in ip:
    ip = ip.split(',')[0].strip()
```

---

## Rollback Plan

If migration fails:

1. **Keep SQLite database** - Don't delete `match_data.db`
2. **Revert code** - `git checkout main -- app.py monitor.py`
3. **Run locally** - Continue with cloudflared tunnel

---

## Cost Summary

| Service | Tier | Cost | Limits |
|---------|------|------|--------|
| Supabase | Free | $0 | 500MB DB, 2GB bandwidth |
| Render | Free | $0 | 750 hrs/mo, sleeps after 15min |
| **Total** | | **$0** | |

**When to upgrade:**
- Supabase Pro ($25/mo): Need >500MB storage, no pausing
- Render Starter ($7/mo): Need always-on, more RAM

---

## Files Changed Summary

| File | Change |
|------|--------|
| `requirements.txt` | New - dependencies |
| `db.py` | New - database connection module |
| `app.py` | Modified - Postgres syntax, add /api/scrape |
| `scraper.py` | Renamed from monitor.py, refactored |
| `.gitignore` | Updated - add .env |

---

## Step-by-Step Execution Order

Follow these steps in order. Each step indicates whether it's a coding task or manual setup.

### Stage 1: External Services Setup

| Step | Type | Task | Notes |
|------|------|------|-------|
| 1.1 | Manual | Create GitHub repo | See Prerequisites §1 |
| 1.2 | Manual | Create Supabase account & project | See Prerequisites §2 |
| 1.3 | Manual | Save connection string | Keep secure, needed for code |
| 1.4 | Manual | Enable pg_cron & pg_net extensions | Run SQL in Supabase |

### Stage 2: Database Schema

| Step | Type | Task | Notes |
|------|------|------|-------|
| 2.1 | Manual | Run schema SQL in Supabase | Copy from Phase 2 section |
| 2.2 | Manual | Verify tables created | Check Table Editor in Supabase |

### Stage 3: Code Changes

| Step | Type | Task | Notes |
|------|------|------|-------|
| 3.1 | Code | Create `requirements.txt` | See Phase 1.1 |
| 3.2 | Code | Create `db.py` | See Phase 3.1 |
| 3.3 | Code | Update `app.py` for Postgres | Change `?` to `%s`, etc. See Phase 3.2 |
| 3.4 | Code | Add `/api/scrape` endpoint | See Phase 3.3 |
| 3.5 | Code | Refactor `monitor.py` → `scraper.py` | See Phase 3.4 |
| 3.6 | Code | Update `.gitignore` | Add `.env`, `*.db` |

### Stage 4: Local Testing

| Step | Type | Task | Notes |
|------|------|------|-------|
| 4.1 | Manual | Create `.env` file locally | DATABASE_URL, SECRET_KEY, etc. |
| 4.2 | Test | Test DB connection | `python -c "from db import get_db; ..."` |
| 4.3 | Test | Test Flask app locally | `python app.py`, visit localhost |
| 4.4 | Test | Test scraper locally | `python scraper.py --once` |
| 4.5 | Test | Test `/api/scrape` endpoint | `curl -X POST localhost:5000/api/scrape?token=...` |

### Stage 5: Data Migration

| Step | Type | Task | Notes |
|------|------|------|-------|
| 5.1 | Code/Manual | Export SQLite data | CSV or SQL dump |
| 5.2 | Manual | Import to Supabase | Via SQL Editor or Table Editor |
| 5.3 | Test | Verify data in Supabase | Check row counts match |

### Stage 6: Deployment

| Step | Type | Task | Notes |
|------|------|------|-------|
| 6.1 | Code | Commit all changes | `git add -A && git commit` |
| 6.2 | Code | Push to GitHub | `git push origin main` |
| 6.3 | Manual | Create Render web service | See Prerequisites §3 |
| 6.4 | Manual | Set environment variables in Render | DATABASE_URL, SECRET_KEY, etc. |
| 6.5 | Manual | Wait for deploy | Watch Render logs |

### Stage 7: Verification & Cron

| Step | Type | Task | Notes |
|------|------|------|-------|
| 7.1 | Test | Test live site | Visit Render URL |
| 7.2 | Test | Test admin login | `/login` with ADMIN_PASSWORD |
| 7.3 | Test | Manual scrape trigger | `curl -X POST https://your-app.onrender.com/api/scrape?token=...` |
| 7.4 | Manual | Create Supabase cron job | See Prerequisites §4 |
| 7.5 | Test | Wait 2-3 mins, check cron logs | `SELECT * FROM cron.job_run_details` |
| 7.6 | Test | Verify new snapshots appearing | Check snapshots table |

### Stage 8: Cleanup

| Step | Type | Task | Notes |
|------|------|------|-------|
| 8.1 | Manual | Stop local monitor.py | Kill any running processes |
| 8.2 | Manual | Stop cloudflared tunnel | No longer needed |
| 8.3 | Optional | Update CLAUDE.md | Document new architecture |
| 8.4 | Optional | Delete local .env | Contains secrets |

---

## Checklist (Quick Reference)

**Setup:**
- [ ] GitHub repo created
- [ ] Supabase project created
- [ ] Connection string saved
- [ ] Extensions enabled (pg_cron, pg_net)
- [ ] Schema SQL executed

**Code:**
- [ ] `requirements.txt` created
- [ ] `db.py` created
- [ ] `app.py` updated for Postgres
- [ ] `/api/scrape` endpoint added
- [ ] `monitor.py` → `scraper.py` refactored
- [ ] `.gitignore` updated

**Testing:**
- [ ] Local testing passed
- [ ] Data migrated to Supabase

**Deployment:**
- [ ] Code pushed to GitHub
- [ ] Render service created
- [ ] Environment variables set
- [ ] Live site working
- [ ] Cron job created
- [ ] End-to-end flow verified
