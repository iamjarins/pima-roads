import os

import psycopg

from .config import DATABASE_URL

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")


def connect():
    return psycopg.connect(DATABASE_URL)


def ensure_schema(conn):
    with open(SCHEMA) as f:
        conn.execute(f.read())
    conn.commit()
