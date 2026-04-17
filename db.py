"""
Database connection module.
Handles Postgres connections for both app.py and scraper.py.
Uses connection pooling to avoid slow reconnects to remote Supabase.
"""
import os
import time
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor, Json
from contextlib import contextmanager

DATABASE_URL = os.environ.get('DATABASE_URL')

# Connection timeout in seconds
CONNECT_TIMEOUT = 10

# Connection pool (initialized lazily)
_connection_pool = None


def _get_pool():
    """Get or create the connection pool."""
    global _connection_pool
    if _connection_pool is None:
        print(f"[DB] Creating connection pool...", flush=True)
        start = time.monotonic()
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            connect_timeout=CONNECT_TIMEOUT
        )
        elapsed = time.monotonic() - start
        print(f"[DB] Connection pool created in {elapsed:.3f}s", flush=True)
    return _connection_pool


@contextmanager
def get_db():
    """Context manager for database connections from pool."""
    start = time.monotonic()
    conn = None
    try:
        conn = _get_pool().getconn()
        elapsed = time.monotonic() - start
        print(f"[DB] Got connection from pool in {elapsed:.3f}s", flush=True)
        yield conn
    except psycopg2.OperationalError as e:
        elapsed = time.monotonic() - start
        print(f"[DB] Connection FAILED after {elapsed:.3f}s: {e}", flush=True)
        raise
    finally:
        if conn:
            _get_pool().putconn(conn)
            print(f"[DB] Connection returned to pool", flush=True)


@contextmanager
def get_cursor(conn):
    """Context manager for database cursors with dict results."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()
