import os
import psycopg2
from psycopg2 import pool
from urllib.parse import urlparse

# Render లో ఉన్న DATABASE_URL ని తీసుకుంటుంది
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None
