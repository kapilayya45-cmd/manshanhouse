import os
import psycopg2
from psycopg2.extras import Json
from contextlib import contextmanager

# Render Environment Variable నుండి URL తీసుకుంటుంది
DATABASE_URL = os.environ.get('DATABASE_URL')

@contextmanager
def get_db():
    conn = None
    try:
        # SSL Mode కచ్చితంగా ఉండాలి
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        yield conn
    except Exception as e:
        print(f"Database Connection Error: {e}")
        raise
    finally:
        if conn:
            conn.close()

@contextmanager
def get_cursor():
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Database Cursor Error: {e}")
            raise
        finally:
            cursor.close()
