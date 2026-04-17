#!/usr/bin/env python3
"""
Monitor script that scrapes cricket match odds and live score every minute.
Stores results in a SQLite database. Supports multiple matches.
"""

import requests
from bs4 import BeautifulSoup
import json
import re
import time
import sqlite3
from datetime import datetime
from pathlib import Path

# Configuration
DB_FILE = Path(__file__).parent / "match_data.db"
INTERVAL_SECONDS = 60


def get_active_events():
    """Load active events from the database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT id, name, event_type, odds_url, score_url, outcomes, allowed_outcomes
        FROM events
        WHERE active = 1 AND archived = 0
    ''')
    rows = c.fetchall()
    conn.close()

    events = []
    for row in rows:
        allowed = []
        if row['allowed_outcomes']:
            try:
                allowed = json.loads(row['allowed_outcomes'])
            except:
                pass
        events.append({
            'id': row['id'],
            'name': row['name'],
            'match_type': row['event_type'],
            'odds_url': row['odds_url'],
            'cricket_url': row['score_url'],
            'outcomes': row['outcomes'] or 2,
            'allowed_outcomes': allowed,
        })
    return events


def init_db():
    """Initialize the SQLite database with required tables."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Matches table - registered matches to track
    c.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            match_type TEXT,
            odds_url TEXT,
            cricket_url TEXT,
            outcomes INTEGER DEFAULT 2
        )
    ''')

    # Snapshots table - one row per scrape per match
    c.execute('''
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            match_status TEXT,
            match_stage TEXT,
            match_state TEXT,
            errors TEXT,
            FOREIGN KEY (match_id) REFERENCES matches(id)
        )
    ''')

    # Odds table - odds for each outcome per snapshot
    c.execute('''
        CREATE TABLE IF NOT EXISTS odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            odds REAL NOT NULL,
            implied_probability REAL NOT NULL,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    ''')

    # Innings table - innings data per snapshot
    c.execute('''
        CREATE TABLE IF NOT EXISTS innings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            team TEXT NOT NULL,
            inning_number INTEGER,
            runs INTEGER,
            wickets INTEGER,
            overs REAL,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    ''')

    # Commentary table - recent ball commentary per snapshot
    c.execute('''
        CREATE TABLE IF NOT EXISTS commentary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            over_number REAL,
            title TEXT,
            text TEXT,
            is_wicket INTEGER,
            runs INTEGER,
            FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
        )
    ''')

    conn.commit()
    conn.close()


def register_matches():
    """Register configured matches in the database."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    for match in MATCHES:
        c.execute('''
            INSERT OR IGNORE INTO matches (name, match_type, odds_url, cricket_url, outcomes)
            VALUES (?, ?, ?, ?, ?)
        ''', (match['name'], match['match_type'], match['odds_url'], match['cricket_url'], match['outcomes']))

    conn.commit()
    conn.close()


def get_match_id(match_name):
    """Get the database ID for a match by name."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT id FROM matches WHERE name = ?', (match_name,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None


def scrape_odds(url, num_outcomes=2, allowed_outcomes=None):
    """Scrape match odds from Sportsbet."""
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    labels = []
    odds_elements = []

    # Try three-outcome pattern (Test matches with draw)
    if num_outcomes == 3:
        labels = soup.select('[data-automation-id$="-three-outcome-label"]')
        odds_elements = soup.select('[data-automation-id$="-three-outcome-text"]')

    # Try two-outcome pattern (T20/ODI without draw)
    if not labels or not odds_elements:
        labels = soup.select('[data-automation-id$="-two-outcome-label"]')
        odds_elements = soup.select('[data-automation-id$="-two-outcome-text"]')

    # Try list-outcome pattern (live matches)
    if not labels or not odds_elements:
        labels = soup.select('[data-automation-id$="-list-outcome-name"]')
        odds_elements = soup.select('[data-automation-id$="-list-outcome-text"]')

    if not labels or not odds_elements:
        return None

    # Build result, filtering by allowed outcomes if specified
    result = {}
    for label, odd in zip(labels, odds_elements):
        outcome_name = label.text.strip()

        # Skip if not in allowed list
        if allowed_outcomes and outcome_name not in allowed_outcomes:
            continue

        result[outcome_name] = {
            'odds': float(odd.text),
        }

    if not result:
        return None

    # Calculate implied probabilities only for the filtered results
    raw_probs = [1 / data['odds'] for data in result.values()]
    total = sum(raw_probs)

    for outcome, data in result.items():
        raw_prob = 1 / data['odds']
        data['implied_probability'] = round(raw_prob / total * 100, 2)

    return result


def scrape_cricket(url):
    """Scrape live cricket data from ESPN Cricinfo."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'sec-ch-ua': '"Google Chrome";v="131"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    next_data = soup.find('script', id='__NEXT_DATA__')

    if not next_data:
        return None

    data = json.loads(next_data.string)

    # Navigate the nested structure safely - may not exist for upcoming matches
    try:
        props = data['props']['appPageProps']['data']['data']
    except (KeyError, TypeError):
        # Match hasn't started yet or different page structure
        return None

    if not props:
        return None

    match = props.get('match', {})
    content = props.get('content', {})

    # Filter out template placeholders from status
    status = match.get('status')
    if status and ('{{' in status or '}}' in status):
        status = None

    result = {
        'status': status,
        'stage': match.get('stage'),
        'state': match.get('state'),
        'teams': [],
        'innings': [],
        'recent_balls': []
    }

    # Teams and scores
    for team_info in match.get('teams') or []:
        team = team_info.get('team') or {}
        result['teams'].append({
            'name': team.get('longName'),
            'abbreviation': team.get('abbreviation'),
            'score': team_info.get('score'),
            'score_info': team_info.get('scoreInfo'),
            'is_batting': team_info.get('isBatting', False)
        })

    # Innings
    for inn in content.get('innings') or []:
        team = inn.get('team') or {}
        result['innings'].append({
            'team': team.get('longName'),
            'inning_number': inn.get('inningNumber'),
            'runs': inn.get('runs'),
            'wickets': inn.get('wickets'),
            'overs': inn.get('overs')
        })

    # Recent commentary (last 5 balls)
    recent_commentary = content.get('recentBallCommentary') or {}
    for comm in (recent_commentary.get('ballComments') or [])[:5]:
        text_items = comm.get('commentTextItems') or []
        text = text_items[0].get('html', '') if text_items else ''
        text = re.sub('<[^>]+>', '', text)

        result['recent_balls'].append({
            'over': comm.get('oversActual'),
            'title': comm.get('title'),
            'text': text[:100] if text else '',
            'is_wicket': comm.get('isWicket', False),
            'runs': comm.get('totalRuns', 0)
        })

    return result


def save_to_db(match_id, timestamp, odds_data, cricket_data, errors):
    """Save scraped data to SQLite database."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Insert snapshot
    c.execute('''
        INSERT INTO snapshots (match_id, timestamp, match_status, match_stage, match_state, errors)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        match_id,
        timestamp,
        cricket_data.get('status') if cricket_data else None,
        cricket_data.get('stage') if cricket_data else None,
        cricket_data.get('state') if cricket_data else None,
        json.dumps(errors) if errors else None
    ))
    snapshot_id = c.lastrowid

    # Insert odds
    if odds_data:
        for outcome, data in odds_data.items():
            c.execute('''
                INSERT INTO odds (snapshot_id, outcome, odds, implied_probability)
                VALUES (?, ?, ?, ?)
            ''', (snapshot_id, outcome, data['odds'], data['implied_probability']))

    # Insert innings
    if cricket_data and cricket_data.get('innings'):
        for inn in cricket_data['innings']:
            c.execute('''
                INSERT INTO innings (snapshot_id, team, inning_number, runs, wickets, overs)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (snapshot_id, inn['team'], inn['inning_number'], inn['runs'], inn['wickets'], inn['overs']))

    # Insert commentary
    if cricket_data and cricket_data.get('recent_balls'):
        for ball in cricket_data['recent_balls']:
            c.execute('''
                INSERT INTO commentary (snapshot_id, over_number, title, text, is_wicket, runs)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (snapshot_id, ball['over'], ball['title'], ball['text'], int(ball['is_wicket']), ball['runs']))

    conn.commit()
    conn.close()

    return snapshot_id


def get_snapshot_count(match_id=None):
    """Get the total number of snapshots in the database."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if match_id:
        c.execute('SELECT COUNT(*) FROM snapshots WHERE match_id = ?', (match_id,))
    else:
        c.execute('SELECT COUNT(*) FROM snapshots')
    count = c.fetchone()[0]
    conn.close()
    return count


def scrape_match(match_config):
    """Scrape a single match."""
    # Use event ID directly if available (from events table), otherwise look up by name
    match_id = match_config.get('id') or get_match_id(match_config['name'])

    if not match_id:
        print(f"  Match '{match_config['name']}' not found in database")
        return None

    odds_data = None
    cricket_data = None
    errors = []

    # Scrape odds
    try:
        odds_data = scrape_odds(
            match_config['odds_url'],
            match_config['outcomes'],
            match_config.get('allowed_outcomes')
        )
        if odds_data:
            odds_str = ', '.join([f"{k}: {v['odds']}" for k, v in odds_data.items()])
            print(f"    Odds: {odds_str}")
    except Exception as e:
        errors.append(f"Odds error: {str(e)}")
        print(f"    Odds error: {e}")

    # Scrape cricket
    try:
        cricket_data = scrape_cricket(match_config['cricket_url'])
        if cricket_data:
            for t in cricket_data['teams']:
                if t['score']:
                    print(f"    {t['name']}: {t['score']}")
            print(f"    Status: {cricket_data['status']}")
    except Exception as e:
        errors.append(f"Cricket error: {str(e)}")
        print(f"    Cricket error: {e}")

    return match_id, odds_data, cricket_data, errors


def run_once():
    """Run a single scrape cycle for all matches."""
    timestamp = datetime.now().isoformat()
    events = get_active_events()
    print(f"[{timestamp}] Scraping {len(events)} active events...")

    if not events:
        print("  No active events to scrape")
        return

    for match_config in events:
        print(f"\n  {match_config['name']}:")

        result = scrape_match(match_config)
        if result:
            match_id, odds_data, cricket_data, errors = result
            snapshot_id = save_to_db(match_id, timestamp, odds_data, cricket_data, errors)
            total = get_snapshot_count(match_id)
            print(f"    Saved snapshot #{snapshot_id} (total for match: {total})")


def run_continuous():
    """Run scraping continuously every minute."""
    print(f"Starting monitor - scraping every {INTERVAL_SECONDS} seconds")
    print(f"Database: {DB_FILE}")
    events = get_active_events()
    print(f"Active events: {len(events)}")
    for m in events:
        print(f"  - {m['name']} ({m['match_type']}, {m['outcomes']} outcomes)")
    print("\nPress Ctrl+C to stop\n")

    while True:
        try:
            run_once()
            print(f"\n  Next scrape in {INTERVAL_SECONDS} seconds...\n")
            time.sleep(INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nStopped by user")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(INTERVAL_SECONDS)


def query_latest(match_name=None, as_json=False):
    """Query and display the latest snapshot(s)."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if match_name:
        c.execute('''
            SELECT s.*, m.name as match_name, m.match_type
            FROM snapshots s
            JOIN matches m ON s.match_id = m.id
            WHERE m.name = ?
            ORDER BY s.id DESC LIMIT 1
        ''', (match_name,))
        snapshots = [c.fetchone()]
    else:
        # Get latest for each match
        c.execute('''
            SELECT s.*, m.name as match_name, m.match_type
            FROM snapshots s
            JOIN matches m ON s.match_id = m.id
            WHERE s.id IN (
                SELECT MAX(id) FROM snapshots GROUP BY match_id
            )
            ORDER BY m.name
        ''')
        snapshots = c.fetchall()

    if not snapshots or not snapshots[0]:
        if as_json:
            print(json.dumps({"matches": []}))
        else:
            print("No data yet")
        return

    if as_json:
        output = {"matches": []}
        for snapshot in snapshots:
            if not snapshot:
                continue
            match_data = {
                "name": snapshot["match_name"],
                "type": snapshot["match_type"],
                "timestamp": snapshot["timestamp"],
                "status": snapshot["match_status"],
                "stage": snapshot["match_stage"],
                "state": snapshot["match_state"],
                "odds": {},
                "innings": []
            }
            # Get odds
            c.execute('SELECT * FROM odds WHERE snapshot_id = ?', (snapshot['id'],))
            for o in c.fetchall():
                match_data["odds"][o["outcome"]] = {
                    "odds": o["odds"],
                    "implied_probability": o["implied_probability"]
                }
            # Get innings
            c.execute('SELECT * FROM innings WHERE snapshot_id = ?', (snapshot['id'],))
            for inn in c.fetchall():
                match_data["innings"].append({
                    "team": inn["team"],
                    "inning_number": inn["inning_number"],
                    "runs": inn["runs"],
                    "wickets": inn["wickets"],
                    "overs": inn["overs"]
                })
            output["matches"].append(match_data)
        conn.close()
        print(json.dumps(output, indent=2))
        return

    for snapshot in snapshots:
        if not snapshot:
            continue

        print(f"\n{'=' * 60}")
        print(f"Match: {snapshot['match_name']}")
        print(f"Snapshot #{snapshot['id']} - {snapshot['timestamp']}")
        print(f"Status: {snapshot['match_status']} | Stage: {snapshot['match_stage']} | State: {snapshot['match_state']}")

        # Get odds
        c.execute('SELECT * FROM odds WHERE snapshot_id = ?', (snapshot['id'],))
        odds = c.fetchall()
        if odds:
            print("\nOdds:")
            for o in odds:
                print(f"  {o['outcome']}: {o['odds']} ({o['implied_probability']}%)")

        # Get innings
        c.execute('SELECT * FROM innings WHERE snapshot_id = ?', (snapshot['id'],))
        innings = c.fetchall()
        if innings:
            print("\nInnings:")
            for inn in innings:
                print(f"  {inn['inning_number']}) {inn['team']}: {inn['runs']}/{inn['wickets']} ({inn['overs']} ov)")

    conn.close()


if __name__ == "__main__":
    import sys

    # Initialize database
    init_db()

    if len(sys.argv) > 1:
        if sys.argv[1] == "--once":
            run_once()
        elif sys.argv[1] == "--query":
            match_name = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "--json" else None
            query_latest(match_name)
        elif sys.argv[1] == "--json":
            match_name = sys.argv[2] if len(sys.argv) > 2 else None
            query_latest(match_name, as_json=True)
        elif sys.argv[1] == "--list":
            events = get_active_events()
            print(f"Active events ({len(events)}):")
            for m in events:
                print(f"  - {m['name']} ({m['match_type']})")
    else:
        run_continuous()
