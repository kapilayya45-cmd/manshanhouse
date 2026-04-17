#!/usr/bin/env python3
"""
Test Supabase connection locally to isolate Render vs Supabase issues.

Usage:
    DATABASE_URL="postgres://..." python test_supabase.py

Or set DATABASE_URL in your environment first.
"""

import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set")
    print("Usage: DATABASE_URL='postgres://...' python test_supabase.py")
    exit(1)

# Mask password for display
display_url = DATABASE_URL
if '@' in display_url:
    parts = display_url.split('@')
    prefix = parts[0].rsplit(':', 1)[0]  # Everything before password
    display_url = f"{prefix}:****@{parts[1]}"

print(f"Testing connection to: {display_url}")
print("=" * 60)

def test_connection(timeout=5):
    """Test basic connection."""
    print(f"\n[TEST 1] Basic connection (timeout={timeout}s)...")
    start = time.monotonic()
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=timeout)
        elapsed = time.monotonic() - start
        print(f"  ✓ Connected in {elapsed:.3f}s")
        conn.close()
        return True
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"  ✗ FAILED after {elapsed:.3f}s: {e}")
        return False

def test_simple_query():
    """Test a simple query."""
    print(f"\n[TEST 2] Simple query (SELECT 1)...")
    start = time.monotonic()
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        connect_time = time.monotonic() - start

        cur = conn.cursor()
        query_start = time.monotonic()
        cur.execute("SELECT 1 as test")
        result = cur.fetchone()
        query_time = time.monotonic() - query_start

        cur.close()
        conn.close()

        print(f"  ✓ Connect: {connect_time:.3f}s, Query: {query_time:.3f}s, Result: {result}")
        return True
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"  ✗ FAILED after {elapsed:.3f}s: {e}")
        return False

def test_events_query():
    """Test querying the events table."""
    print(f"\n[TEST 3] Events table query...")
    start = time.monotonic()
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        connect_time = time.monotonic() - start

        cur = conn.cursor(cursor_factory=RealDictCursor)
        query_start = time.monotonic()
        cur.execute("SELECT id, name, active FROM events ORDER BY id")
        rows = cur.fetchall()
        query_time = time.monotonic() - query_start

        cur.close()
        conn.close()

        print(f"  ✓ Connect: {connect_time:.3f}s, Query: {query_time:.3f}s")
        print(f"  Found {len(rows)} events:")
        for row in rows:
            status = "active" if row['active'] else "inactive"
            print(f"    - [{row['id']}] {row['name']} ({status})")
        return True
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"  ✗ FAILED after {elapsed:.3f}s: {e}")
        return False

def test_snapshots_count():
    """Test counting snapshots (potentially large table)."""
    print(f"\n[TEST 4] Snapshots count (large table test)...")
    start = time.monotonic()
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        connect_time = time.monotonic() - start

        cur = conn.cursor()
        query_start = time.monotonic()
        cur.execute("SELECT COUNT(*) FROM snapshots")
        count = cur.fetchone()[0]
        query_time = time.monotonic() - query_start

        cur.close()
        conn.close()

        print(f"  ✓ Connect: {connect_time:.3f}s, Query: {query_time:.3f}s")
        print(f"  Snapshots count: {count}")
        return True
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"  ✗ FAILED after {elapsed:.3f}s: {e}")
        return False

def test_history_query(match_id=18):
    """Test the heavy history query."""
    print(f"\n[TEST 5] History query for match_id={match_id}...")
    start = time.monotonic()
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        connect_time = time.monotonic() - start

        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Get snapshots
        query_start = time.monotonic()
        cur.execute("""
            SELECT id, timestamp, match_status
            FROM snapshots
            WHERE match_id = %s
            ORDER BY timestamp
        """, (match_id,))
        snapshots = cur.fetchall()
        snapshots_time = time.monotonic() - query_start

        snapshot_ids = [s['id'] for s in snapshots]

        if snapshot_ids:
            # Get odds
            odds_start = time.monotonic()
            cur.execute("""
                SELECT snapshot_id, outcome, odds
                FROM odds
                WHERE snapshot_id = ANY(%s)
            """, (snapshot_ids,))
            odds = cur.fetchall()
            odds_time = time.monotonic() - odds_start

            # Get innings
            innings_start = time.monotonic()
            cur.execute("""
                SELECT snapshot_id, team, runs, wickets, overs
                FROM innings
                WHERE snapshot_id = ANY(%s)
            """, (snapshot_ids,))
            innings = cur.fetchall()
            innings_time = time.monotonic() - innings_start
        else:
            odds = []
            innings = []
            odds_time = 0
            innings_time = 0

        cur.close()
        conn.close()

        total_time = time.monotonic() - start

        print(f"  ✓ Connect: {connect_time:.3f}s")
        print(f"  ✓ Snapshots query: {snapshots_time:.3f}s ({len(snapshots)} rows)")
        print(f"  ✓ Odds query: {odds_time:.3f}s ({len(odds)} rows)")
        print(f"  ✓ Innings query: {innings_time:.3f}s ({len(innings)} rows)")
        print(f"  ✓ TOTAL: {total_time:.3f}s")
        return True
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"  ✗ FAILED after {elapsed:.3f}s: {e}")
        return False

def test_concurrent_connections(n=5):
    """Test multiple concurrent connections."""
    print(f"\n[TEST 6] Concurrent connections (n={n})...")
    import threading

    results = []

    def connect_and_query(thread_id):
        start = time.monotonic()
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
            conn.close()
            elapsed = time.monotonic() - start
            results.append((thread_id, True, elapsed))
        except Exception as e:
            elapsed = time.monotonic() - start
            results.append((thread_id, False, elapsed, str(e)))

    threads = []
    start = time.monotonic()
    for i in range(n):
        t = threading.Thread(target=connect_and_query, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    total = time.monotonic() - start

    successes = sum(1 for r in results if r[1])
    print(f"  ✓ {successes}/{n} connections succeeded in {total:.3f}s total")
    for r in results:
        if r[1]:
            print(f"    Thread {r[0]}: {r[2]:.3f}s")
        else:
            print(f"    Thread {r[0]}: FAILED after {r[2]:.3f}s - {r[3]}")

    return successes == n

if __name__ == '__main__':
    print("\nStarting Supabase connection tests...\n")

    results = []
    results.append(("Basic connection", test_connection()))
    results.append(("Simple query", test_simple_query()))
    results.append(("Events query", test_events_query()))
    results.append(("Snapshots count", test_snapshots_count()))
    results.append(("History query", test_history_query()))
    results.append(("Concurrent connections", test_concurrent_connections()))

    print("\n" + "=" * 60)
    print("SUMMARY:")
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")

    all_passed = all(r[1] for r in results)
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed!"))
