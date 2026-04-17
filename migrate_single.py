#!/usr/bin/env python3
"""Migrate a single match from SQLite to Postgres."""

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

MATCH_ID = 2  # BBL: Sixers vs Strikers
SQLITE_DB = 'match_data.db'
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("Error: DATABASE_URL not set")
    sys.exit(1)

print(f"Migrating match ID {MATCH_ID}...")

sqlite_conn = sqlite3.connect(SQLITE_DB)
sqlite_conn.row_factory = sqlite3.Row
pg_conn = psycopg2.connect(DATABASE_URL)

# Migrate event
print("  Migrating event...")
sqlite_cur = sqlite_conn.cursor()
sqlite_cur.execute("SELECT * FROM events WHERE id = ?", (MATCH_ID,))
row = dict(sqlite_cur.fetchone())

allowed = row.get('allowed_outcomes')
if isinstance(allowed, str) and allowed:
    try:
        allowed = json.loads(allowed)
    except:
        allowed = None

pg_cur = pg_conn.cursor()
pg_cur.execute("""
    INSERT INTO events (id, name, event_type, odds_url, score_url, outcomes,
                       allowed_outcomes, active, archived, start_date, end_date, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
""", (
    row['id'], row['name'], row.get('event_type'), row.get('odds_url'),
    row.get('score_url'), row.get('outcomes', 2),
    Json(allowed) if allowed else None,
    bool(row.get('active', 1)), bool(row.get('archived', 0)),
    row.get('start_date'), row.get('end_date'), row.get('created_at')
))
pg_conn.commit()
print("    Done")

# Get snapshot IDs for this match
print("  Migrating snapshots...")
sqlite_cur.execute("SELECT * FROM snapshots WHERE match_id = ?", (MATCH_ID,))
snapshots = [dict(r) for r in sqlite_cur.fetchall()]
snapshot_ids = [s['id'] for s in snapshots]
print(f"    Found {len(snapshots)} snapshots")

if snapshots:
    values = [(s['id'], s['match_id'], s['timestamp'], s.get('match_status'),
               s.get('match_stage'), s.get('match_state'), s.get('errors')) for s in snapshots]
    execute_values(pg_cur, """
        INSERT INTO snapshots (id, match_id, timestamp, match_status, match_stage, match_state, errors)
        VALUES %s ON CONFLICT (id) DO NOTHING
    """, values)
    pg_conn.commit()

# Migrate odds
print("  Migrating odds...")
placeholders = ','.join('?' * len(snapshot_ids))
sqlite_cur.execute(f"SELECT * FROM odds WHERE snapshot_id IN ({placeholders})", snapshot_ids)
odds = [dict(r) for r in sqlite_cur.fetchall()]
print(f"    Found {len(odds)} odds")

if odds:
    values = [(o['id'], o['snapshot_id'], o['outcome'], o['odds'], o['implied_probability']) for o in odds]
    execute_values(pg_cur, """
        INSERT INTO odds (id, snapshot_id, outcome, odds, implied_probability)
        VALUES %s ON CONFLICT (id) DO NOTHING
    """, values)
    pg_conn.commit()

# Migrate innings
print("  Migrating innings...")
sqlite_cur.execute(f"SELECT * FROM innings WHERE snapshot_id IN ({placeholders})", snapshot_ids)
innings = [dict(r) for r in sqlite_cur.fetchall()]
print(f"    Found {len(innings)} innings")

if innings:
    values = [(i['id'], i['snapshot_id'], i['team'], i.get('inning_number'),
               i.get('runs'), i.get('wickets'), i.get('overs')) for i in innings]
    execute_values(pg_cur, """
        INSERT INTO innings (id, snapshot_id, team, inning_number, runs, wickets, overs)
        VALUES %s ON CONFLICT (id) DO NOTHING
    """, values)
    pg_conn.commit()

# Migrate commentary
print("  Migrating commentary...")
sqlite_cur.execute(f"SELECT * FROM commentary WHERE snapshot_id IN ({placeholders})", snapshot_ids)
commentary = [dict(r) for r in sqlite_cur.fetchall()]
print(f"    Found {len(commentary)} commentary")

if commentary:
    values = [(c['id'], c['snapshot_id'], c.get('over_number'), c.get('title'),
               c.get('text'), bool(c.get('is_wicket', 0)), c.get('runs')) for c in commentary]
    execute_values(pg_cur, """
        INSERT INTO commentary (id, snapshot_id, over_number, title, text, is_wicket, runs)
        VALUES %s ON CONFLICT (id) DO NOTHING
    """, values)
    pg_conn.commit()

sqlite_conn.close()
pg_conn.close()

print("\nDone!")
