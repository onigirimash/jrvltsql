import os
from contextlib import contextmanager

import pg8000.dbapi as pg

_DB_CONFIG: dict = {
    "host":     os.environ.get("POSTGRES_HOST",     "localhost"),
    "port":     int(os.environ.get("POSTGRES_PORT", "5432")),
    "database": os.environ.get("POSTGRES_DATABASE", "keiba"),
    "user":     os.environ.get("POSTGRES_USER",     "postgres"),
    "password": os.environ.get("PGPASSWORD",        ""),
}


@contextmanager
def get_conn():
    conn = pg.connect(**_DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def to_dicts(cursor) -> list[dict]:
    if not cursor.description:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
