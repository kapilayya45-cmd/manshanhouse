import os
import psycopg2
from psycopg2.extras import Json
from contextlib import contextmanager

# ఇక్కడ మీ URLని నేరుగా ఇస్తున్నాను (Render లో రాకపోయినా ఇది పని చేస్తుంది)
DATABASE_URL = os.environ.get('DATABASE_URL', "postgresql://postgres.hfnqiycyreyuugejslvl:JXcus6ddzpv0u2L5@://supabase.com")

@contextmanager
def get_db():
    conn = None
    try:
        # sslmode=require అనేది నెట్‌వర్క్ ఎర్రర్ రాకుండా చేస్తుంది
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        yield conn
    except Exception as e:
        print(f"Database Connection Error: {e}")
        raise
    finally:
        if conn:
            conn.close()

# మిగతా get_cursor ఫంక్షన్ అలాగే ఉంచండి...
