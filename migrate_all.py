#!/usr/bin/env python3
"""Migrate all data from SQLite to Postgres."""

import os
import sys
import sqlite3
import json

try:
    import psycopg2
    from psycopg2.extras import execute_values, Json
except ImportError:
    print("Error: psycopg2 not installed")
    sys.exit(1)

SQLITE_DB = 'match_data.db'
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    sys.exit(1)

print("Connecting to databases...")
sqlite_conn = sqlite3.connect(SQLITE_DB)
sqlite_conn.row_factory = sqlite3.Row
pg_conn = psycopg2.connect(DATABASE_URL)
pg_cur = pg_conn.cursor()

# Get all events
sqlite_cur = sqlite_conn.cursor()
sqlite_cur.execute("SELECT * FROM events")
events = [dict(r) for r in sqlite_cur.fetchall()]
print(f"Found {len(events)} events to migrate")

for event in events:
    match_id = event['id']
    print(f"\nMigrating: {event['name']} (ID: {match_id})")

    # Parse JSON fields
    allowed = event.get('allowed_outcomes')
    if isinstance(allowed, str) and allowed:
        try:
            allowed = json.loads(allowed)
        except:
            allowed = None

    colors = event.get('outcome_colors')
    if isinstance(colors, str) and colors:
        try:
            colors = json.loads(colors)
        except:
            colors = None

    order = event.get('outcome_order')
    if isinstance(order, str) and order:
        try:
            order = json.loads(order)
        except:
            order = None

    # Insert event
    pg_cur.execute("""
        INSERT INTO events (id, name, event_type, odds_url, score_url, outcomes,
                           allowed_outcomes, outcome_colors, outcome_order,
                           active, archived, start_date, end_date, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            outcome_colors = EXCLUDED.outcome_colors,
            outcome_order = EXCLUDED.outcome_order
    """, (
        event['id'], event['name'], event.get('event_type'), event.get('odds_url'),
        event.get('score_url'), event.get('outcomes', 2),
        Json(allowed) if allowed else None,
        Json(colors) if colors else None,
        Json(order) if order else None,
        bool(event.get('active', 1)), bool(event.get('archived', 0)),
        event.get('start_date'), event.get('end_date'), event.get('created_at')
    ))
    pg_conn.commit()
    print("  Event done")

    # Get snapshots
    sqlite_cur.execute("SELECT * FROM snapshots WHERE match_id = ?", (match_id,))
    snapshots = [dict(r) for r in sqlite_cur.fetchall()]
    snapshot_ids = [s['id'] for s in snapshots]
    print(f"  {len(snapshots)} snapshots...")

    if snapshots:
        values = [(s['id'], s['match_id'], s['timestamp'], s.get('match_status'),
                   s.get('match_stage'), s.get('match_state'), s.get('errors')) for s in snapshots]
        execute_values(pg_cur, """
            INSERT INTO snapshots (id, match_id, timestamp, match_status, match_stage, match_state, errors)
            VALUES %s ON CONFLICT (id) DO NOTHING
        """, values)
        pg_conn.commit()

    if not snapshot_ids:
        continue

    # Get odds
    placeholders = ','.join('?' * len(snapshot_ids))
    sqlite_cur.execute(f"SELECT * FROM odds WHERE snapshot_id IN ({placeholders})", snapshot_ids)
    odds = [dict(r) for r in sqlite_cur.fetchall()]
    print(f"  {len(odds)} odds...")

    if odds:
        values = [(o['id'], o['snapshot_id'], o['outcome'], o['odds'], o['implied_probability']) for o in odds]
        execute_values(pg_cur, """
            INSERT INTO odds (id, snapshot_id, outcome, odds, implied_probability)
            VALUES %s ON CONFLICT (id) DO NOTHING
        """, values)
        pg_conn.commit()

    # Get innings
    sqlite_cur.execute(f"SELECT * FROM innings WHERE snapshot_id IN ({placeholders})", snapshot_ids)
    innings = [dict(r) for r in sqlite_cur.fetchall()]
    print(f"  {len(innings)} innings...")

    if innings:
        values = [(i['id'], i['snapshot_id'], i['team'], i.get('inning_number'),
                   i.get('runs'), i.get('wickets'), i.get('overs')) for i in innings]
        execute_values(pg_cur, """
            INSERT INTO innings (id, snapshot_id, team, inning_number, runs, wickets, overs)
            VALUES %s ON CONFLICT (id) DO NOTHING
        """, values)
        pg_conn.commit()

# Reset sequences to max id + 1
print("\nResetting sequences...")
pg_cur.execute("SELECT setval('events_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM events), false)")
pg_cur.execute("SELECT setval('snapshots_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM snapshots), false)")
pg_cur.execute("SELECT setval('odds_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM odds), false)")
pg_cur.execute("SELECT setval('innings_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM innings), false)")
pg_conn.commit()

sqlite_conn.close()
pg_conn.close()

print("\nMigration complete!")
