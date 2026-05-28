import os
from contextlib import contextmanager

import pg8000.dbapi as pg

_DB_CONFIG: dict = {
    "host": "localhost",
    "port": 5432,
    "database": "keiba",
    "user": "postgres",
    "password": os.environ.get("PGPASSWORD", ""),
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


def to_dict(cursor) -> dict | None:
    if not cursor.description:
        return None
    cols = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return dict(zip(cols, row)) if row else None
