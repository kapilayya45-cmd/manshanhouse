#!/usr/bin/env python3
"""
One-time migration script: SQLite → Supabase Postgres

Usage:
    1. Set DATABASE_URL environment variable to your Supabase connection string
    2. Run schema.sql in Supabase SQL Editor first
    3. Run: python migrate.py
"""

import os
import sys
import sqlite3
import json

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, execute_values, Json
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary", flush=True)
    sys.exit(1)

SQLITE_DB = 'match_data.db'
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable not set", flush=True)
    sys.exit(1)

if not os.path.exists(SQLITE_DB):
    print(f"Error: SQLite database '{SQLITE_DB}' not found", flush=True)
    sys.exit(1)


def migrate_events(sqlite_conn, pg_conn):
    """Migrate events table."""
    print("  Migrating events...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT * FROM events")
    rows = sqlite_cur.fetchall()
    print(f"    Found {len(rows)} events in SQLite", flush=True)

    if not rows:
        print("    No events to migrate", flush=True)
        return

    pg_cur.execute("DELETE FROM events")
    print("    Cleared existing events in Postgres", flush=True)

    for i, row in enumerate(rows):
        row = dict(row)  # Convert to dict for .get() access
        # Parse allowed_outcomes JSON
        allowed = row.get('allowed_outcomes')
        if isinstance(allowed, str) and allowed:
            try:
                allowed = json.loads(allowed)
            except:
                allowed = None

        pg_cur.execute("""
            INSERT INTO events (id, name, event_type, odds_url, score_url, outcomes,
                               allowed_outcomes, active, archived, start_date, end_date, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            row['id'],
            row['name'],
            row.get('event_type'),
            row.get('odds_url'),
            row.get('score_url'),
            row.get('outcomes', 2),
            Json(allowed) if allowed else None,
            bool(row.get('active', 1)),
            bool(row.get('archived', 0)),
            row.get('start_date'),
            row.get('end_date'),
            row.get('created_at')
        ))

    pg_conn.commit()
    print(f"    Migrated {len(rows)} events", flush=True)


def migrate_snapshots(sqlite_conn, pg_conn):
    """Migrate snapshots table using batch insert."""
    print("  Migrating snapshots...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT COUNT(*) FROM snapshots")
    total = sqlite_cur.fetchone()[0]
    print(f"    Total snapshots: {total}", flush=True)

    if total == 0:
        return

    pg_cur.execute("DELETE FROM snapshots")
    pg_conn.commit()

    # Batch insert
    batch_size = 1000
    offset = 0
    migrated = 0

    while offset < total:
        sqlite_cur.execute(f"SELECT * FROM snapshots LIMIT {batch_size} OFFSET {offset}")
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        values = []
        for row in rows:
            r = dict(row)
            values.append((
                r['id'],
                r['match_id'],
                r['timestamp'],
                r.get('match_status'),
                r.get('match_stage'),
                r.get('match_state'),
                r.get('errors')
            ))

        execute_values(pg_cur, """
            INSERT INTO snapshots (id, match_id, timestamp, match_status, match_stage, match_state, errors)
            VALUES %s
        """, values)

        pg_conn.commit()
        migrated += len(rows)
        offset += batch_size
        print(f"    Progress: {migrated}/{total}", flush=True)

    print(f"    Migrated {migrated} snapshots", flush=True)


def migrate_odds(sqlite_conn, pg_conn):
    """Migrate odds table using batch insert."""
    print("  Migrating odds...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT COUNT(*) FROM odds")
    total = sqlite_cur.fetchone()[0]
    print(f"    Total odds: {total}", flush=True)

    if total == 0:
        return

    pg_cur.execute("DELETE FROM odds")
    pg_conn.commit()

    batch_size = 1000
    offset = 0
    migrated = 0

    while offset < total:
        sqlite_cur.execute(f"SELECT * FROM odds LIMIT {batch_size} OFFSET {offset}")
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        values = []
        for row in rows:
            r = dict(row)
            values.append((
                r['id'],
                r['snapshot_id'],
                r['outcome'],
                r['odds'],
                r['implied_probability']
            ))

        execute_values(pg_cur, """
            INSERT INTO odds (id, snapshot_id, outcome, odds, implied_probability)
            VALUES %s
        """, values)

        pg_conn.commit()
        migrated += len(rows)
        offset += batch_size
        print(f"    Progress: {migrated}/{total}", flush=True)

    print(f"    Migrated {migrated} odds", flush=True)


def migrate_innings(sqlite_conn, pg_conn):
    """Migrate innings table using batch insert."""
    print("  Migrating innings...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT COUNT(*) FROM innings")
    total = sqlite_cur.fetchone()[0]
    print(f"    Total innings: {total}", flush=True)

    if total == 0:
        return

    pg_cur.execute("DELETE FROM innings")
    pg_conn.commit()

    batch_size = 1000
    offset = 0
    migrated = 0

    while offset < total:
        sqlite_cur.execute(f"SELECT * FROM innings LIMIT {batch_size} OFFSET {offset}")
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        values = []
        for row in rows:
            r = dict(row)
            values.append((
                r['id'],
                r['snapshot_id'],
                r['team'],
                r.get('inning_number'),
                r.get('runs'),
                r.get('wickets'),
                r.get('overs')
            ))

        execute_values(pg_cur, """
            INSERT INTO innings (id, snapshot_id, team, inning_number, runs, wickets, overs)
            VALUES %s
        """, values)

        pg_conn.commit()
        migrated += len(rows)
        offset += batch_size
        print(f"    Progress: {migrated}/{total}", flush=True)

    print(f"    Migrated {migrated} innings", flush=True)


def migrate_commentary(sqlite_conn, pg_conn):
    """Migrate commentary table."""
    print("  Migrating commentary...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    # Check if table exists
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commentary'")
    if not sqlite_cur.fetchone():
        print("    Table doesn't exist, skipping", flush=True)
        return

    sqlite_cur.execute("SELECT COUNT(*) FROM commentary")
    total = sqlite_cur.fetchone()[0]
    print(f"    Total commentary: {total}", flush=True)

    if total == 0:
        return

    pg_cur.execute("DELETE FROM commentary")
    pg_conn.commit()

    batch_size = 1000
    offset = 0
    migrated = 0

    while offset < total:
        sqlite_cur.execute(f"SELECT * FROM commentary LIMIT {batch_size} OFFSET {offset}")
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        values = []
        for row in rows:
            r = dict(row)
            values.append((
                r['id'],
                r['snapshot_id'],
                r.get('over_number'),
                r.get('title'),
                r.get('text'),
                bool(r.get('is_wicket', 0)),
                r.get('runs')
            ))

        execute_values(pg_cur, """
            INSERT INTO commentary (id, snapshot_id, over_number, title, text, is_wicket, runs)
            VALUES %s
        """, values)

        pg_conn.commit()
        migrated += len(rows)
        offset += batch_size
        print(f"    Progress: {migrated}/{total}", flush=True)

    print(f"    Migrated {migrated} commentary", flush=True)


def migrate_request_log(sqlite_conn, pg_conn):
    """Migrate request_log table."""
    print("  Migrating request_log...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT COUNT(*) FROM request_log")
    total = sqlite_cur.fetchone()[0]
    print(f"    Total request_log: {total}", flush=True)

    if total == 0:
        return

    pg_cur.execute("DELETE FROM request_log")
    pg_conn.commit()

    batch_size = 1000
    offset = 0
    migrated = 0

    while offset < total:
        sqlite_cur.execute(f"SELECT * FROM request_log LIMIT {batch_size} OFFSET {offset}")
        rows = sqlite_cur.fetchall()

        if not rows:
            break

        values = []
        for row in rows:
            r = dict(row)
            values.append((
                r['id'],
                r['timestamp'],
                r.get('path'),
                r.get('ip'),
                r.get('user_agent'),
                r.get('request_type', 'page')
            ))

        execute_values(pg_cur, """
            INSERT INTO request_log (id, timestamp, path, ip, user_agent, request_type)
            VALUES %s
        """, values)

        pg_conn.commit()
        migrated += len(rows)
        offset += batch_size
        if migrated % 5000 == 0:
            print(f"    Progress: {migrated}/{total}", flush=True)

    print(f"    Migrated {migrated} request_log", flush=True)


def migrate_known_ips(sqlite_conn, pg_conn):
    """Migrate known_ips table."""
    print("  Migrating known_ips...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='known_ips'")
    if not sqlite_cur.fetchone():
        print("    Table doesn't exist, skipping", flush=True)
        return

    sqlite_cur.execute("SELECT * FROM known_ips")
    rows = sqlite_cur.fetchall()

    if not rows:
        print("    No data to migrate", flush=True)
        return

    pg_cur.execute("DELETE FROM known_ips")

    for row in rows:
        r = dict(row)
        pg_cur.execute("INSERT INTO known_ips (ip, owner) VALUES (%s, %s)",
                      (r['ip'], r.get('owner')))

    pg_conn.commit()
    print(f"    Migrated {len(rows)} known_ips", flush=True)


def migrate_ip_locations(sqlite_conn, pg_conn):
    """Migrate ip_locations table."""
    print("  Migrating ip_locations...", flush=True)
    sqlite_cur = sqlite_conn.cursor()
    pg_cur = pg_conn.cursor()

    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ip_locations'")
    if not sqlite_cur.fetchone():
        print("    Table doesn't exist, skipping", flush=True)
        return

    sqlite_cur.execute("SELECT * FROM ip_locations")
    rows = sqlite_cur.fetchall()

    if not rows:
        print("    No data to migrate", flush=True)
        return

    pg_cur.execute("DELETE FROM ip_locations")

    for row in rows:
        r = dict(row)
        pg_cur.execute("""
            INSERT INTO ip_locations (ip, country, country_code, city, fetched_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (r['ip'], r.get('country'), r.get('country_code'),
              r.get('city'), r.get('fetched_at')))

    pg_conn.commit()
    print(f"    Migrated {len(rows)} ip_locations", flush=True)


def reset_sequences(pg_conn):
    """Reset Postgres sequences to max ID + 1."""
    print("  Resetting sequences...", flush=True)
    pg_cur = pg_conn.cursor()

    sequences = [
        ('events', 'id'),
        ('snapshots', 'id'),
        ('odds', 'id'),
        ('innings', 'id'),
        ('commentary', 'id'),
        ('request_log', 'id'),
    ]

    for table, column in sequences:
        try:
            pg_cur.execute(f"SELECT MAX({column}) FROM {table}")
            max_id = pg_cur.fetchone()[0] or 0
            seq_name = f"{table}_{column}_seq"
            pg_cur.execute(f"SELECT setval('{seq_name}', %s)", (max(max_id, 1),))
        except Exception as e:
            print(f"    Warning: Could not reset sequence for {table}: {e}", flush=True)
            pg_conn.rollback()

    pg_conn.commit()
    print("    Done", flush=True)


def main():
    print("=" * 60, flush=True)
    print("SQLite → Supabase Migration", flush=True)
    print("=" * 60, flush=True)

    # Connect
    print("\nConnecting to databases...", flush=True)
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(DATABASE_URL)
    print("  Connected!", flush=True)

    # Disable FK constraints temporarily
    pg_cur = pg_conn.cursor()
    pg_cur.execute("SET session_replication_role = 'replica';")
    pg_conn.commit()
    print("  Disabled FK constraints", flush=True)

    # Migrate tables in order (respecting foreign keys)
    print("\nMigrating tables...", flush=True)
    migrate_events(sqlite_conn, pg_conn)
    migrate_snapshots(sqlite_conn, pg_conn)
    migrate_odds(sqlite_conn, pg_conn)
    migrate_innings(sqlite_conn, pg_conn)
    migrate_commentary(sqlite_conn, pg_conn)
    migrate_request_log(sqlite_conn, pg_conn)
    migrate_known_ips(sqlite_conn, pg_conn)
    migrate_ip_locations(sqlite_conn, pg_conn)

    # Re-enable FK constraints
    pg_cur = pg_conn.cursor()
    pg_cur.execute("SET session_replication_role = 'origin';")
    pg_conn.commit()
    print("\n  Re-enabled FK constraints", flush=True)

    print("\nResetting sequences...", flush=True)
    reset_sequences(pg_conn)

    # Close
    sqlite_conn.close()
    pg_conn.close()

    print("\n" + "=" * 60, flush=True)
    print("Migration complete!", flush=True)
    print("=" * 60, flush=True)


if __name__ == '__main__':
    main()
