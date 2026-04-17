#!/usr/bin/env python3
"""
Flask web app to display odds time series charts.
LOCAL VERSION - Uses SQLite for local development.
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, session
import sqlite3
from pathlib import Path
import os
import json
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')
DB_FILE = Path(__file__).parent / "match_data.db"


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@app.before_request
def log_request():
    # Skip static files and stats
    if request.path.startswith('/static') or request.path == '/stats':
        return

    # Determine request type
    if request.path.startswith('/api/'):
        req_type = 'refresh'
    else:
        req_type = 'page'

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO request_log (timestamp, path, ip, user_agent, request_type)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(),
        request.path,
        request.headers.get('X-Forwarded-For', request.remote_addr),
        request.headers.get('User-Agent', '')[:200],
        req_type
    ))
    conn.commit()
    conn.close()


@app.route('/stats')
def stats():
    conn = get_db()
    c = conn.cursor()

    # Total requests by type
    c.execute("SELECT COUNT(*) FROM request_log WHERE request_type = 'page'")
    total_pages = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM request_log WHERE request_type = 'refresh'")
    total_refreshes = c.fetchone()[0]

    # Unique IPs
    c.execute('SELECT COUNT(DISTINCT ip) FROM request_log')
    unique_ips = c.fetchone()[0]

    # Today's requests by type
    c.execute("SELECT COUNT(*) FROM request_log WHERE timestamp >= date('now') AND request_type = 'page'")
    today_pages = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM request_log WHERE timestamp >= date('now') AND request_type = 'refresh'")
    today_refreshes = c.fetchone()[0]

    # Unique IPs today
    c.execute("SELECT COUNT(DISTINCT ip) FROM request_log WHERE timestamp >= date('now')")
    today_ips = c.fetchone()[0]

    # Breakdown by IP (pages and refreshes per IP, today)
    c.execute('''
        SELECT ip,
               SUM(CASE WHEN request_type = 'page' THEN 1 ELSE 0 END) as pages,
               SUM(CASE WHEN request_type = 'refresh' THEN 1 ELSE 0 END) as refreshes
        FROM request_log
        WHERE timestamp >= date('now')
        GROUP BY ip
        ORDER BY (pages + refreshes) DESC
    ''')
    ip_breakdown = [{'ip': r['ip'], 'pages': r['pages'], 'refreshes': r['refreshes']} for r in c.fetchall()]

    # Recent requests
    c.execute('''
        SELECT timestamp, path, ip, user_agent, request_type
        FROM request_log
        ORDER BY id DESC
        LIMIT 20
    ''')
    recent = [{'time': r['timestamp'], 'path': r['path'], 'ip': r['ip'], 'ua': r['user_agent'], 'type': r['request_type']} for r in c.fetchall()]

    conn.close()

    return jsonify({
        'total_pages': total_pages,
        'total_refreshes': total_refreshes,
        'unique_ips': unique_ips,
        'today_pages': today_pages,
        'today_refreshes': today_refreshes,
        'today_unique_ips': today_ips,
        'ip_breakdown': ip_breakdown,
        'recent': recent
    })


@app.after_request
def add_ngrok_header(response):
    response.headers['ngrok-skip-browser-warning'] = 'true'
    return response


@app.route('/')
def index():
    conn = get_db()
    c = conn.cursor()
    # Get live events (active and not archived)
    c.execute('SELECT id, name, event_type as match_type, start_date FROM events WHERE active = 1 AND archived = 0 ORDER BY name')
    live_matches = [dict(row) for row in c.fetchall()]
    # Get archived events sorted by end_date descending
    c.execute('SELECT id, name, event_type as match_type, end_date FROM events WHERE archived = 1 ORDER BY end_date DESC')
    archived_matches = [dict(row) for row in c.fetchall()]
    conn.close()
    return render_template('index.html', live_matches=live_matches, archived_matches=archived_matches)


@app.route('/visualizations')
def visualizations():
    return render_template('visualizations.html')


@app.route('/archive-options')
def archive_options():
    return render_template('archive_options.html')


@app.route('/api/matches')
def api_matches():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, event_type as match_type FROM events ORDER BY name')
    matches = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(matches)


def get_match_state_key(innings):
    """Create a hashable key representing the match state (scores, overs, wickets)."""
    if not innings:
        return ()
    return tuple(
        (inn['inning_number'], inn['runs'], inn['wickets'], inn['overs'])
        for inn in sorted(innings, key=lambda x: x['inning_number'])
    )


def get_odds_key(odds):
    """Create a hashable key representing the odds."""
    if not odds:
        return ()
    return tuple(sorted((k, v['odds']) for k, v in odds.items()))


def calculate_cumulative_overs(innings):
    """Calculate total overs bowled across all innings."""
    if not innings:
        return 0
    return sum(inn['overs'] or 0 for inn in innings)


def get_total_wickets(innings):
    """Get total wickets across all innings."""
    if not innings:
        return 0
    return sum(inn['wickets'] or 0 for inn in innings)


@app.route('/api/match/<int:match_id>/history')
def api_match_history(match_id):
    conn = get_db()
    c = conn.cursor()

    # Get match info
    c.execute('SELECT id, name, event_type as match_type, start_date, end_date, outcome_colors, outcome_order FROM events WHERE id = ?', (match_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Match not found'}), 404
    match = dict(row)
    # Parse JSON fields
    if match.get('outcome_colors'):
        try:
            match['outcome_colors'] = json.loads(match['outcome_colors'])
        except:
            match['outcome_colors'] = {}
    if match.get('outcome_order'):
        try:
            match['outcome_order'] = json.loads(match['outcome_order'])
        except:
            match['outcome_order'] = []

    # Get all snapshots for this match
    c.execute('''
        SELECT id, timestamp, match_status, match_stage, match_state
        FROM snapshots
        WHERE match_id = ?
        ORDER BY timestamp
    ''', (match_id,))
    snapshots = c.fetchall()
    snapshot_ids = [s['id'] for s in snapshots]

    if not snapshot_ids:
        conn.close()
        return jsonify({'match': match, 'history': [], 'events': [], 'innings_boundaries': [0]})

    # Bulk fetch all odds for these snapshots
    placeholders = ','.join('?' * len(snapshot_ids))
    c.execute(f'''
        SELECT snapshot_id, outcome, odds, implied_probability
        FROM odds
        WHERE snapshot_id IN ({placeholders})
    ''', snapshot_ids)
    odds_by_snapshot = {}
    for o in c.fetchall():
        sid = o['snapshot_id']
        if sid not in odds_by_snapshot:
            odds_by_snapshot[sid] = {}
        odds_by_snapshot[sid][o['outcome']] = {
            'odds': o['odds'],
            'implied_probability': o['implied_probability']
        }

    # Bulk fetch all innings for these snapshots
    c.execute(f'''
        SELECT snapshot_id, team, inning_number, runs, wickets, overs
        FROM innings
        WHERE snapshot_id IN ({placeholders})
    ''', snapshot_ids)
    innings_by_snapshot = {}
    for inn in c.fetchall():
        sid = inn['snapshot_id']
        if sid not in innings_by_snapshot:
            innings_by_snapshot[sid] = []
        innings_by_snapshot[sid].append({
            'team': inn['team'],
            'inning_number': inn['inning_number'],
            'runs': inn['runs'],
            'wickets': inn['wickets'],
            'overs': inn['overs']
        })

    conn.close()

    # Build raw points from pre-fetched data
    raw_points = []
    for snapshot in snapshots:
        sid = snapshot['id']
        point = {
            'timestamp': snapshot['timestamp'],
            'status': snapshot['match_status'],
            'stage': snapshot['match_stage'],
            'state': snapshot['match_state'],
            'odds': odds_by_snapshot.get(sid, {}),
            'innings': innings_by_snapshot.get(sid, [])
        }
        raw_points.append(point)

    # Deduplicate based on (cumulative_overs, odds) - keep most recent score at each over
    data_points = []
    events = []  # Track wickets and innings changes
    innings_boundaries = [0]  # Cumulative overs at start of each innings
    prev_odds_key = None
    prev_cumulative_overs = None
    prev_innings_count = None
    prev_total_wickets = None
    micro_increment = 0

    for point in raw_points:
        odds_key = get_odds_key(point['odds'])
        cumulative_overs = calculate_cumulative_overs(point['innings'])
        current_innings_count = len(point['innings'])
        current_total_wickets = get_total_wickets(point['innings'])

        # Skip if both overs AND odds are identical to previous
        if cumulative_overs == prev_cumulative_overs and odds_key == prev_odds_key:
            continue

        # Determine x-position
        if prev_cumulative_overs is None or cumulative_overs != prev_cumulative_overs:
            # Overs changed - use actual cumulative overs
            micro_increment = 0
            x_position = cumulative_overs
        else:
            # Same overs but odds changed (e.g., during a break)
            # Use tiny increment (0.001) so it doesn't look like a ball was bowled
            micro_increment += 0.001
            x_position = cumulative_overs + micro_increment

        point['x'] = round(x_position, 3)
        point['cumulative_overs'] = cumulative_overs

        # Track events (only after first point)
        if prev_innings_count is not None and current_innings_count > prev_innings_count:
            # Record boundary at the END of previous innings (prev_cumulative_overs)
            if prev_cumulative_overs is not None:
                innings_boundaries.append(prev_cumulative_overs)
            events.append({
                'x': x_position,
                'type': 'innings',
                'label': f'Innings {current_innings_count}'
            })

        if prev_total_wickets is not None and current_total_wickets > prev_total_wickets:
            wickets_fallen = current_total_wickets - prev_total_wickets
            for _ in range(wickets_fallen):
                events.append({
                    'x': x_position,
                    'type': 'wicket',
                    'label': 'Wicket'
                })

        data_points.append(point)
        prev_odds_key = odds_key
        prev_cumulative_overs = cumulative_overs
        prev_innings_count = current_innings_count
        prev_total_wickets = current_total_wickets

    return jsonify({
        'match': match,
        'history': data_points,
        'events': events,
        'innings_boundaries': innings_boundaries
    })


# --- Admin Authentication ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        error = 'Invalid password'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))


@app.route('/admin')
@login_required
def admin():
    return redirect(url_for('admin_events'))


@app.route('/admin/events')
@login_required
def admin_events():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT e.id, e.name, e.start_date, e.event_type, e.odds_url, e.score_url, e.cricbuzz_url, e.score_source,
               e.outcomes, e.allowed_outcomes, e.outcome_colors, e.outcome_order, e.active, e.archived, e.end_date, e.created_at,
               s.timestamp as last_scrape, s.errors as last_errors
        FROM events e
        LEFT JOIN (
            SELECT match_id, timestamp, errors,
                   ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY id DESC) as rn
            FROM snapshots
        ) s ON e.id = s.match_id AND s.rn = 1
        ORDER BY e.active DESC, e.archived ASC, e.name
    ''')
    events = []
    for row in c.fetchall():
        event = dict(row)
        # Calculate time ago
        if event.get('last_scrape'):
            try:
                scrape_time = datetime.fromisoformat(event['last_scrape'])
                delta = datetime.now() - scrape_time
                if delta.total_seconds() < 60:
                    event['last_scrape_ago'] = 'just now'
                elif delta.total_seconds() < 3600:
                    mins = int(delta.total_seconds() / 60)
                    event['last_scrape_ago'] = f'{mins}m ago'
                elif delta.total_seconds() < 86400:
                    hours = int(delta.total_seconds() / 3600)
                    event['last_scrape_ago'] = f'{hours}h ago'
                else:
                    days = int(delta.total_seconds() / 86400)
                    event['last_scrape_ago'] = f'{days}d ago'
            except:
                event['last_scrape_ago'] = ''
        events.append(event)
    conn.close()
    return render_template('admin.html', events=events)


@app.route('/admin/visitors')
@login_required
def admin_visitors():
    return render_template('visitors.html')


@app.route('/api/admin/visitors')
@login_required
def api_admin_visitors():
    conn = get_db()
    c = conn.cursor()

    # Get known IPs
    c.execute('SELECT ip, owner FROM known_ips')
    known_ips = {row['ip']: row['owner'] for row in c.fetchall()}

    # Get cached locations
    c.execute('SELECT ip, country, country_code, city FROM ip_locations')
    locations = {row['ip']: dict(row) for row in c.fetchall()}

    # Get unique visitors with aggregated data
    c.execute('''
        SELECT
            ip,
            MIN(timestamp) as first_seen,
            MAX(timestamp) as last_seen,
            COUNT(*) as total_requests,
            SUM(CASE WHEN request_type = 'page' THEN 1 ELSE 0 END) as page_views,
            GROUP_CONCAT(DISTINCT user_agent) as user_agents,
            GROUP_CONCAT(DISTINCT path) as paths
        FROM request_log
        GROUP BY ip
        ORDER BY last_seen DESC
    ''')

    visitors = []
    for row in c.fetchall():
        visitor = dict(row)
        visitor['is_known'] = row['ip'] in known_ips
        visitor['owner'] = known_ips.get(row['ip'], '')

        # Add location data
        loc = locations.get(row['ip'])
        if loc:
            visitor['location'] = {
                'country': loc['country'],
                'country_code': loc['country_code'],
                'city': loc['city']
            }
        else:
            visitor['location'] = None

        # Parse user agents to get unique ones (limit to first 3)
        uas = row['user_agents'].split(',') if row['user_agents'] else []
        visitor['user_agents'] = uas[:3]

        # Classify as bot based on user agent
        ua_lower = (row['user_agents'] or '').lower()
        visitor['is_bot'] = any(bot in ua_lower for bot in [
            'bot', 'spider', 'crawler', 'facebook', 'twitter',
            'slack', 'discord', 'telegram', 'whatsapp', 'preview',
            'curl', 'wget', 'python', 'go-http', 'java/'
        ])

        visitors.append(visitor)

    conn.close()

    # Count IPs missing location
    missing_locations = sum(1 for v in visitors if not v['location'] and not v['ip'].startswith(('127.', '192.168.', '10.')))

    return jsonify({'visitors': visitors, 'known_ips': known_ips, 'missing_locations': missing_locations})


@app.route('/api/admin/known-ips', methods=['POST'])
@login_required
def api_add_known_ip():
    data = request.get_json()
    ip = data.get('ip')
    owner = data.get('owner', '')

    if not ip:
        return jsonify({'error': 'IP required'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO known_ips (ip, owner) VALUES (?, ?)', (ip, owner))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/admin/known-ips/<path:ip>', methods=['DELETE'])
@login_required
def api_remove_known_ip(ip):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM known_ips WHERE ip = ?', (ip,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/admin/fetch-locations', methods=['POST'])
@login_required
def api_fetch_locations():
    import requests as req

    conn = get_db()
    c = conn.cursor()
    # Get IPs without cached location
    c.execute('''
        SELECT DISTINCT r.ip
        FROM request_log r
        LEFT JOIN ip_locations l ON r.ip = l.ip
        WHERE l.ip IS NULL
          AND r.ip NOT LIKE '127.%'
          AND r.ip NOT LIKE '192.168.%'
          AND r.ip NOT LIKE '10.%'
        LIMIT 100
    ''')
    ips = [row['ip'] for row in c.fetchall()]
    conn.close()

    if not ips:
        return jsonify({'fetched': 0, 'message': 'All IPs already have locations'})

    # Batch request to ip-api.com
    try:
        resp = req.post(
            'http://ip-api.com/batch?fields=status,query,country,countryCode,city',
            json=ips,
            timeout=10
        )
        results = resp.json()

        fetched = 0
        conn = get_db()
        c = conn.cursor()
        for data in results:
            if data.get('status') == 'success':
                c.execute('''
                    INSERT OR REPLACE INTO ip_locations (ip, country, country_code, city, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (data['query'], data.get('country'), data.get('countryCode'), data.get('city'), datetime.now().isoformat()))
                fetched += 1
        conn.commit()
        conn.close()
        return jsonify({'fetched': fetched, 'total': len(ips)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events', methods=['GET'])
@login_required
def api_events_list():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM events ORDER BY active DESC, name')
    events = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(events)


@app.route('/api/events', methods=['POST'])
@login_required
def api_events_create():
    data = request.get_json()
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO events (name, start_date, event_type, odds_url, score_url, cricbuzz_url, score_source, outcomes, allowed_outcomes, outcome_colors, outcome_order, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('name'),
        data.get('start_date'),
        data.get('event_type'),
        data.get('odds_url'),
        data.get('score_url'),
        data.get('cricbuzz_url'),
        data.get('score_source', 'cricinfo'),
        data.get('outcomes', 2),
        json.dumps(data.get('allowed_outcomes', [])),
        json.dumps(data.get('outcome_colors', {})),
        json.dumps(data.get('outcome_order', [])),
        1 if data.get('active', True) else 0
    ))
    event_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': event_id, 'success': True})


@app.route('/api/events/<int:event_id>', methods=['PUT'])
@login_required
def api_events_update(event_id):
    data = request.get_json()
    print(f"DEBUG: Updating event {event_id}")
    print(f"DEBUG: outcome_colors = {data.get('outcome_colors')}")
    print(f"DEBUG: outcome_order = {data.get('outcome_order')}")
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        UPDATE events
        SET name = ?, start_date = ?, event_type = ?, odds_url = ?, score_url = ?, cricbuzz_url = ?,
            score_source = ?, outcomes = ?, allowed_outcomes = ?, outcome_colors = ?, outcome_order = ?,
            active = ?, archived = ?, end_date = ?
        WHERE id = ?
    ''', (
        data.get('name'),
        data.get('start_date'),
        data.get('event_type'),
        data.get('odds_url'),
        data.get('score_url'),
        data.get('cricbuzz_url'),
        data.get('score_source', 'cricinfo'),
        data.get('outcomes', 2),
        json.dumps(data.get('allowed_outcomes', [])),
        json.dumps(data.get('outcome_colors', {})),
        json.dumps(data.get('outcome_order', [])),
        1 if data.get('active', True) else 0,
        1 if data.get('archived', False) else 0,
        data.get('end_date'),
        event_id
    ))
    print(f"DEBUG: Rows updated = {c.rowcount}")
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/events/<int:event_id>', methods=['DELETE'])
@login_required
def api_events_delete(event_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM events WHERE id = ?', (event_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/admin/scrape', methods=['POST'])
@login_required
def api_admin_scrape():
    """Trigger a scrape cycle from admin panel. Captures debug output."""
    import io
    import sys

    # Capture stdout to get debug info
    old_stdout = sys.stdout
    captured_output = io.StringIO()
    sys.stdout = captured_output

    try:
        from scraper import run_once
        results = run_once()
        success = True
        error = None
    except Exception as e:
        results = []
        success = False
        error = str(e)
    finally:
        sys.stdout = old_stdout

    debug_output = captured_output.getvalue()

    return jsonify({
        'success': success,
        'events_scraped': len(results),
        'results': results,
        'debug': debug_output,
        'error': error
    })


if __name__ == '__main__':
    app.run(debug=True, port=5001)
