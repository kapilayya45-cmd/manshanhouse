#!/usr/bin/env python3
"""
Flask web app to display odds time series charts.
"""

from flask import Flask, render_template, jsonify, request, redirect, url_for, session, g
from db import get_db, get_cursor, Json
import os
import json
import time
import sys
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')

# Force unbuffered output for real-time logging
print("[APP] Flask app starting...", flush=True)

@app.route('/health')
def health():
    """Lightweight health check endpoint - no database access."""
    print(f"[HEALTH] Health check at {time.strftime('%H:%M:%S')}", flush=True)
    return 'OK', 200


@app.route('/debug/db')
def debug_db():
    """Test database connection and return timing info."""
    print(f"[DEBUG] /debug/db called at {time.strftime('%H:%M:%S')}", flush=True)
    start = time.monotonic()
    try:
        with get_db() as conn:
            connect_time = time.monotonic() - start
            print(f"[DEBUG] Connected in {connect_time:.3f}s", flush=True)

            query_start = time.monotonic()
            with get_cursor(conn) as c:
                c.execute('SELECT COUNT(*) as cnt FROM events')
                count = c.fetchone()['cnt']
            query_time = time.monotonic() - query_start
            print(f"[DEBUG] Query done in {query_time:.3f}s", flush=True)

        total_time = time.monotonic() - start
        return jsonify({
            'success': True,
            'connect_time': round(connect_time, 3),
            'query_time': round(query_time, 3),
            'total_time': round(total_time, 3),
            'events_count': count
        })
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"[DEBUG] FAILED after {elapsed:.3f}s: {e}", flush=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'elapsed': round(elapsed, 3)
        }), 500


@app.route('/debug/info')
def debug_info():
    """Return debug info about the running process."""
    import threading
    return jsonify({
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'thread_count': threading.active_count(),
        'threads': [t.name for t in threading.enumerate()],
        'pid': os.getpid()
    })


@app.route('/test-cricbuzz')
def test_cricbuzz():
    """Test if we can reach Cricbuzz from this server."""
    import requests as req
    url = 'https://www.cricbuzz.com/live-cricket-scores/108801/aus-vs-eng-3rd-test-the-ashes-2025-26'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }
    try:
        resp = req.get(url, headers=headers, timeout=10)
        return jsonify({
            'status': resp.status_code,
            'length': len(resp.text),
            'success': resp.status_code == 200,
            'snippet': resp.text[:500] if resp.status_code == 200 else resp.text
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False})


@app.route('/test-sportsbet')
def test_sportsbet():
    """Test if we can reach Sportsbet from this server."""
    import requests as req
    url = 'https://www.sportsbet.com.au/betting/cricket/test-matches/new-zealand-v-west-indies-9943398'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }
    try:
        resp = req.get(url, headers=headers, timeout=10)
        has_odds = 'outcome-label' in resp.text or 'outcome-text' in resp.text
        has_geo_block = 'not available in your region' in resp.text.lower() or 'access denied' in resp.text.lower()
        return jsonify({
            'status': resp.status_code,
            'length': len(resp.text),
            'has_odds_elements': has_odds,
            'appears_geo_blocked': has_geo_block,
            'snippet': resp.text[:1000]
        })
    except Exception as e:
        return jsonify({'error': str(e), 'success': False})

@app.before_request
def track_request_start():
    """Track when each request starts for duration logging."""
    g.request_start = time.monotonic()
    g.request_id = f"{time.time():.0f}"
    print(f"[REQ:{g.request_id}] START {request.method} {request.path}", flush=True)


@app.after_request
def track_request_end(response):
    """Log request duration after completion."""
    if hasattr(g, 'request_start'):
        duration = time.monotonic() - g.request_start
        req_id = getattr(g, 'request_id', '?')
        print(f"[REQ:{req_id}] END {request.path} -> {response.status_code} in {duration:.3f}s", flush=True)
    return response


@app.before_request
def log_request():
    from flask import request
    from datetime import datetime
    # Skip static files, stats, and health checks
    if request.path.startswith('/static') or request.path in ('/stats', '/health'):
        return

    # Determine request type
    if request.path.startswith('/api/'):
        req_type = 'refresh'
    else:
        req_type = 'page'

    req_id = getattr(g, 'request_id', '?')
    print(f"[REQ:{req_id}] Logging request to DB...", flush=True)
    log_start = time.monotonic()

    try:
        with get_db() as conn:
            with get_cursor(conn) as c:
                c.execute('''
                    INSERT INTO request_log (timestamp, path, ip, user_agent, request_type)
                    VALUES (NOW(), %s, %s, %s, %s)
                ''', (
                    request.path,
                    request.headers.get('X-Forwarded-For', request.remote_addr),
                    request.headers.get('User-Agent', '')[:200],
                    req_type
                ))
            conn.commit()
        log_duration = time.monotonic() - log_start
        print(f"[REQ:{req_id}] Request logged in {log_duration:.3f}s", flush=True)
    except Exception as e:
        log_duration = time.monotonic() - log_start
        print(f"[REQ:{req_id}] Request logging FAILED after {log_duration:.3f}s: {e}", flush=True)
        # Don't block the request if logging fails
        pass

@app.route('/stats')
def stats():
    with get_db() as conn:
        with get_cursor(conn) as c:
            # Total requests by type
            c.execute("SELECT COUNT(*) FROM request_log WHERE request_type = 'page'")
            total_pages = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM request_log WHERE request_type = 'refresh'")
            total_refreshes = c.fetchone()['count']

            # Unique IPs
            c.execute('SELECT COUNT(DISTINCT ip) FROM request_log')
            unique_ips = c.fetchone()['count']

            # Today's requests by type
            c.execute("SELECT COUNT(*) FROM request_log WHERE timestamp >= CURRENT_DATE AND request_type = 'page'")
            today_pages = c.fetchone()['count']
            c.execute("SELECT COUNT(*) FROM request_log WHERE timestamp >= CURRENT_DATE AND request_type = 'refresh'")
            today_refreshes = c.fetchone()['count']

            # Unique IPs today
            c.execute("SELECT COUNT(DISTINCT ip) FROM request_log WHERE timestamp >= CURRENT_DATE")
            today_ips = c.fetchone()['count']

            # Breakdown by IP (pages and refreshes per IP, today)
            c.execute('''
                SELECT ip,
                       SUM(CASE WHEN request_type = 'page' THEN 1 ELSE 0 END) as pages,
                       SUM(CASE WHEN request_type = 'refresh' THEN 1 ELSE 0 END) as refreshes
                FROM request_log
                WHERE timestamp >= CURRENT_DATE
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
            recent = [{'time': str(r['timestamp']), 'path': r['path'], 'ip': r['ip'], 'ua': r['user_agent'], 'type': r['request_type']} for r in c.fetchall()]

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
    req_id = getattr(g, 'request_id', '?')
    print(f"[REQ:{req_id}] INDEX: Fetching events from DB...", flush=True)
    db_start = time.monotonic()
    with get_db() as conn:
        with get_cursor(conn) as c:
            # Get live events (active and not archived)
            c.execute('SELECT id, name, event_type as match_type, start_date FROM events WHERE active = true AND archived = false ORDER BY name')
            live_matches = [dict(row) for row in c.fetchall()]
            # Get archived events sorted by end_date descending
            c.execute('SELECT id, name, event_type as match_type, end_date FROM events WHERE archived = true ORDER BY end_date DESC')
            archived_matches = [dict(row) for row in c.fetchall()]
    db_duration = time.monotonic() - db_start
    print(f"[REQ:{req_id}] INDEX: DB done in {db_duration:.3f}s, live={len(live_matches)}, archived={len(archived_matches)}", flush=True)
    print(f"[REQ:{req_id}] INDEX: Rendering template...", flush=True)
    return render_template('index.html', live_matches=live_matches, archived_matches=archived_matches)


@app.route('/visualizations')
def visualizations():
    return render_template('visualizations.html')


@app.route('/archive-options')
def archive_options():
    return render_template('archive_options.html')


@app.route('/api/matches')
def api_matches():
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('SELECT id, name, event_type as match_type FROM events ORDER BY name')
            matches = [dict(row) for row in c.fetchall()]
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
    req_id = getattr(g, 'request_id', '?')
    print(f"[REQ:{req_id}] HISTORY: Starting for match_id={match_id}", flush=True)

    db_start = time.monotonic()
    with get_db() as conn:
        with get_cursor(conn) as c:
            # Get match info
            print(f"[REQ:{req_id}] HISTORY: Fetching match info...", flush=True)
            c.execute('SELECT id, name, event_type as match_type, start_date, end_date, outcome_colors, outcome_order FROM events WHERE id = %s', (match_id,))
            match = dict(c.fetchone())
            # Parse outcome_colors if it's a JSON string
            if match.get('outcome_colors') and isinstance(match['outcome_colors'], str):
                try:
                    match['outcome_colors'] = json.loads(match['outcome_colors'])
                except:
                    match['outcome_colors'] = {}
            # Parse outcome_order if it's a JSON string
            if match.get('outcome_order') and isinstance(match['outcome_order'], str):
                try:
                    match['outcome_order'] = json.loads(match['outcome_order'])
                except:
                    match['outcome_order'] = []

            # Get all snapshots for this match
            c.execute('''
                SELECT id, timestamp, match_status, match_stage, match_state
                FROM snapshots
                WHERE match_id = %s
                ORDER BY timestamp
            ''', (match_id,))
            snapshots = c.fetchall()
            snapshot_ids = [s['id'] for s in snapshots]

            if not snapshot_ids:
                return jsonify({'match': match, 'history': [], 'events': [], 'innings_boundaries': [0]})

            # Bulk fetch all odds for these snapshots
            c.execute('''
                SELECT snapshot_id, outcome, odds, implied_probability
                FROM odds
                WHERE snapshot_id = ANY(%s)
            ''', (snapshot_ids,))
            odds_by_snapshot = {}
            for o in c.fetchall():
                sid = o['snapshot_id']
                if sid not in odds_by_snapshot:
                    odds_by_snapshot[sid] = {}
                odds_by_snapshot[sid][o['outcome']] = {
                    'odds': float(o['odds']) if o['odds'] else None,
                    'implied_probability': float(o['implied_probability']) if o['implied_probability'] else None
                }

            # Bulk fetch all innings for these snapshots
            c.execute('''
                SELECT snapshot_id, team, inning_number, runs, wickets, overs
                FROM innings
                WHERE snapshot_id = ANY(%s)
            ''', (snapshot_ids,))
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
                    'overs': float(inn['overs']) if inn['overs'] else None
                })

    db_duration = time.monotonic() - db_start
    print(f"[REQ:{req_id}] HISTORY: DB queries done in {db_duration:.3f}s, snapshots={len(snapshots)}", flush=True)

    process_start = time.monotonic()
    # Build raw points from pre-fetched data
    raw_points = []
    for snapshot in snapshots:
        sid = snapshot['id']
        point = {
            'timestamp': str(snapshot['timestamp']),
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
    prev_innings_wickets = {}  # Track wickets per innings to identify which team lost wicket
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

        # Detect wickets per-innings to identify which team lost the wicket
        for inn in point['innings']:
            inn_num = inn['inning_number']
            current_wickets = inn['wickets']
            prev_wickets = prev_innings_wickets.get(inn_num, 0)
            if current_wickets > prev_wickets:
                for _ in range(current_wickets - prev_wickets):
                    events.append({
                        'x': x_position,
                        'type': 'wicket',
                        'label': 'Wicket',
                        'team': inn['team']
                    })
            prev_innings_wickets[inn_num] = current_wickets

        data_points.append(point)
        prev_odds_key = odds_key
        prev_cumulative_overs = cumulative_overs
        prev_innings_count = current_innings_count
        prev_total_wickets = current_total_wickets

    process_duration = time.monotonic() - process_start
    total_duration = time.monotonic() - db_start
    print(f"[REQ:{req_id}] HISTORY: Processing done in {process_duration:.3f}s, data_points={len(data_points)}", flush=True)
    print(f"[REQ:{req_id}] HISTORY: Total time={total_duration:.3f}s, returning response...", flush=True)

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
    from datetime import datetime
    req_id = getattr(g, 'request_id', '?')
    print(f"[REQ:{req_id}] ADMIN_EVENTS: Fetching events from DB...", flush=True)
    db_start = time.monotonic()
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('''
                SELECT e.id, e.name, e.start_date, e.event_type, e.odds_url, e.score_url, e.cricbuzz_url, e.scorecard_url, e.score_source,
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
                        scrape_time = event['last_scrape']
                        if isinstance(scrape_time, str):
                            scrape_time = datetime.fromisoformat(scrape_time)
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
    db_duration = time.monotonic() - db_start
    print(f"[REQ:{req_id}] ADMIN_EVENTS: DB done in {db_duration:.3f}s, events={len(events)}", flush=True)
    print(f"[REQ:{req_id}] ADMIN_EVENTS: Rendering template...", flush=True)
    return render_template('admin.html', events=events)


@app.route('/admin/visitors')
@login_required
def admin_visitors():
    return render_template('visitors.html')


@app.route('/api/admin/visitors')
@login_required
def api_admin_visitors():
    from datetime import datetime
    with get_db() as conn:
        with get_cursor(conn) as c:
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
                    STRING_AGG(DISTINCT user_agent, ',') as user_agents,
                    STRING_AGG(DISTINCT path, ',') as paths
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

    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('''
                INSERT INTO known_ips (ip, owner)
                VALUES (%s, %s)
                ON CONFLICT (ip) DO UPDATE SET owner = EXCLUDED.owner
            ''', (ip, owner))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/admin/known-ips/<path:ip>', methods=['DELETE'])
@login_required
def api_remove_known_ip(ip):
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('DELETE FROM known_ips WHERE ip = %s', (ip,))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/admin/fetch-locations', methods=['POST'])
@login_required
def api_fetch_locations():
    import requests
    from datetime import datetime

    with get_db() as conn:
        with get_cursor(conn) as c:
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

    if not ips:
        return jsonify({'fetched': 0, 'message': 'All IPs already have locations'})

    # Batch request to ip-api.com
    try:
        resp = requests.post(
            'http://ip-api.com/batch?fields=status,query,country,countryCode,city',
            json=ips,
            timeout=10
        )
        results = resp.json()

        fetched = 0
        with get_db() as conn:
            with get_cursor(conn) as c:
                for data in results:
                    if data.get('status') == 'success':
                        c.execute('''
                            INSERT INTO ip_locations (ip, country, country_code, city, fetched_at)
                            VALUES (%s, %s, %s, %s, NOW())
                            ON CONFLICT (ip) DO UPDATE
                            SET country = EXCLUDED.country,
                                country_code = EXCLUDED.country_code,
                                city = EXCLUDED.city,
                                fetched_at = EXCLUDED.fetched_at
                        ''', (data['query'], data.get('country'), data.get('countryCode'), data.get('city')))
                        fetched += 1
            conn.commit()
        return jsonify({'fetched': fetched, 'total': len(ips)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/events', methods=['GET'])
@login_required
def api_events_list():
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('SELECT * FROM events ORDER BY active DESC, name')
            events = [dict(row) for row in c.fetchall()]
    return jsonify(events)


@app.route('/api/events', methods=['POST'])
@login_required
def api_events_create():
    data = request.get_json()
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('''
                INSERT INTO events (name, start_date, event_type, odds_url, score_url, cricbuzz_url, scorecard_url, score_source, outcomes, allowed_outcomes, outcome_colors, outcome_order, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (
                data.get('name'),
                data.get('start_date'),
                data.get('event_type'),
                data.get('odds_url'),
                data.get('score_url'),
                data.get('cricbuzz_url'),
                data.get('scorecard_url'),
                data.get('score_source', 'cricinfo'),
                data.get('outcomes', 2),
                Json(data.get('allowed_outcomes', [])),
                Json(data.get('outcome_colors', {})),
                Json(data.get('outcome_order', [])),
                bool(data.get('active', True))
            ))
            event_id = c.fetchone()['id']
        conn.commit()
    return jsonify({'id': event_id, 'success': True})


@app.route('/api/events/<int:event_id>', methods=['PUT'])
@login_required
def api_events_update(event_id):
    data = request.get_json()
    print(f"DEBUG: Updating event {event_id}")
    print(f"DEBUG: allowed_outcomes = {data.get('allowed_outcomes')}")
    print(f"DEBUG: outcome_colors = {data.get('outcome_colors')}")
    print(f"DEBUG: outcome_order = {data.get('outcome_order')}")
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('''
                UPDATE events
                SET name = %s, start_date = %s, event_type = %s, odds_url = %s, score_url = %s, cricbuzz_url = %s,
                    scorecard_url = %s, score_source = %s, outcomes = %s, allowed_outcomes = %s, outcome_colors = %s,
                    outcome_order = %s, active = %s, archived = %s, end_date = %s
                WHERE id = %s
            ''', (
                data.get('name'),
                data.get('start_date'),
                data.get('event_type'),
                data.get('odds_url'),
                data.get('score_url'),
                data.get('cricbuzz_url'),
                data.get('scorecard_url'),
                data.get('score_source', 'cricinfo'),
                data.get('outcomes', 2),
                Json(data.get('allowed_outcomes', [])),
                Json(data.get('outcome_colors', {})),
                Json(data.get('outcome_order', [])),
                bool(data.get('active', True)),
                bool(data.get('archived', False)),
                data.get('end_date'),
                event_id
            ))
            print(f"DEBUG: Rows updated = {c.rowcount}")
        conn.commit()
        print("DEBUG: Commit done")

    # Verify the update
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('SELECT allowed_outcomes, outcome_colors, outcome_order FROM events WHERE id = %s', (event_id,))
            row = c.fetchone()
            print(f"DEBUG: After update - allowed={row['allowed_outcomes']}, colors={row['outcome_colors']}, order={row['outcome_order']}")

    return jsonify({'success': True})


@app.route('/api/events/<int:event_id>', methods=['DELETE'])
@login_required
def api_events_delete(event_id):
    with get_db() as conn:
        with get_cursor(conn) as c:
            c.execute('DELETE FROM events WHERE id = %s', (event_id,))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    """Trigger a scrape cycle. Protected by token authentication."""
    token = request.args.get('token')
    if not token:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

    expected_token = os.environ.get('SCRAPE_TOKEN')
    if not expected_token or token != expected_token:
        return jsonify({'error': 'Unauthorized'}), 401

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


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

