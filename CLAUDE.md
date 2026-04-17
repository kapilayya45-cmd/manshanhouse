# Cricket Odds Tracker

A web application that scrapes and visualizes live cricket betting odds alongside match scores, allowing users to see how odds change throughout a match.

## Project Overview

This project scrapes betting odds from Sportsbet.com.au and live cricket scores from ESPN Cricinfo, storing them in a SQLite database and displaying them as interactive time-series charts.

## Tech Stack

- **Backend**: Python/Flask
- **Database**: SQLite
- **Frontend**: HTML/CSS/JavaScript with Chart.js
- **Scraping**: BeautifulSoup, Requests
- **Hosting**: Cloudflare Tunnel (cloudflared) for public access

## Key Files

### `monitor.py`
The scraping engine that runs continuously, collecting data every 60 seconds.

**Match Configuration** (line ~20):
```python
MATCHES = [
    {
        'name': 'Ashes 3rd Test',
        'match_type': 'test',
        'odds_url': 'https://...',
        'cricket_url': 'https://...',
        'outcomes': 3,  # Win/Draw/Win
        'allowed_outcomes': ['Australia', 'England', 'Draw'],
    },
]
```

**Important**: The `allowed_outcomes` whitelist is critical - it prevents unwanted betting markets (Yes/No prop bets, Over/Under, Double Chance) from polluting the data.

**Selector patterns for Sportsbet** (line ~141):
- `-three-outcome-label/text`: For 3-way markets (Test matches with Draw)
- `-two-outcome-label/text`: For 2-way markets (T20/ODI)
- `-list-outcome-name/text`: For live match pages (different structure)

### `app.py`
Flask web server providing:
- `/` - Main visualization page
- `/api/match/<id>/history` - JSON API for chart data
- `/stats` - Usage analytics
- `/archive-options` - Demo page for archive UI options

**Request tracking** (line ~37): Logs all requests with IP, timestamp, path, user_agent, and request_type ('page' vs 'refresh').

**Known IPs table**: `known_ips` table stores owner-identified IPs to filter from visitor stats.

### `templates/index.html`
Main frontend with:
- Chart.js visualization with zoom/pan support (chartjs-plugin-zoom, hammerjs)
- Vertical lines for wickets (dashed red) and innings changes (solid yellow)
- X-axis toggle between cumulative overs and time
- Team color mapping (Australia=yellow, England=blue, Draw=red)
- Mobile-responsive tooltip below chart
- Live/Archive match selector (buttons for live, dropdown for archived)

### Database Schema (`match_data.db`)

**matches**: id, name, match_type, odds_url, cricket_url, archived, end_date

**snapshots**: id, match_id, timestamp, match_status, match_stage, match_state

**odds**: id, snapshot_id, outcome, odds, implied_probability

**innings**: id, snapshot_id, team, runs, wickets, overs, innings_number

**request_log**: id, timestamp, path, ip, user_agent, request_type

**known_ips**: ip, owner

## Data Processing

### Deduplication
The API (`/api/match/<id>/history`) deduplicates data points where both odds AND match state are identical, to avoid storing redundant data during breaks in play.

### X-axis Calculation
Uses cumulative overs across innings, with micro-increments (0.001) for odds changes that occur at the same over (e.g., during breaks).

### Innings-relative labels
X-axis labels show overs relative to current innings (resetting at innings change) rather than cumulative overs.

## Running the Project

### Start the web server:
```bash
./venv/bin/python app.py
```
Access at http://localhost:5000

### Start the scraper:
```bash
./venv/bin/python monitor.py
```
Or for a single scrape: `./venv/bin/python monitor.py --once`

### Public hosting with Cloudflare Tunnel:
```bash
./cloudflared tunnel --url http://localhost:5000
```

## Archiving Matches

When a match finishes:

1. Remove from `MATCHES` list in `monitor.py`
2. Delete data after last odds change:
```sql
DELETE FROM odds WHERE snapshot_id IN (SELECT id FROM snapshots WHERE match_id = X AND id > LAST_ODDS_SNAPSHOT);
DELETE FROM snapshots WHERE match_id = X AND id > LAST_ODDS_SNAPSHOT;
```
3. Mark as archived:
```sql
UPDATE matches SET archived = 1, end_date = 'YYYY-MM-DD' WHERE id = X;
```

## Common Issues

### Unwanted outcomes appearing (Yes/No, Over/Under, etc.)
- **Cause**: Sportsbet page has multiple betting markets using same HTML selectors
- **Fix**: Ensure `allowed_outcomes` whitelist is set in match config
- **Clean**: `DELETE FROM odds WHERE outcome NOT IN ('Team1', 'Team2', 'Draw') AND snapshot_id IN (SELECT id FROM snapshots WHERE match_id = X)`
- **Prevention**: Restart monitor.py after config changes

### Live match URL changes
Sportsbet sometimes uses different URLs for pre-match vs live. Check the actual URL when match goes live and update in `MATCHES` config.

### Selector patterns change
Sportsbet uses different HTML structures:
- Pre-match: `-two-outcome-*` or `-three-outcome-*`
- Live: `-list-outcome-*`

## Usage Analytics

Query real visitors (excluding bots and known IPs):
```sql
SELECT ip, MIN(timestamp), MAX(timestamp), COUNT(*)
FROM request_log
WHERE ip NOT IN (SELECT ip FROM known_ips)
  AND ip NOT LIKE '2a03:2880%'  -- Facebook
  AND ip NOT LIKE '74.125%'     -- Google
  AND ip NOT LIKE '64.233%'     -- Google
  AND ip NOT LIKE '104.210%'    -- Microsoft
  AND timestamp >= date('now')
GROUP BY ip;
```

Geolocate IPs:
```bash
curl -s "http://ip-api.com/json/IP_ADDRESS"
```

## Virtual Environment

Dependencies installed in `./venv/`:
- flask
- requests
- beautifulsoup4

Activate: `source venv/bin/activate` or use `./venv/bin/python` directly.
