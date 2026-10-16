import os
from pathlib import Path
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://swimmable:swimmable@localhost:5432/swimmable")

@contextmanager
def connection():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as db:
        yield db


def migrate():
    with connection() as db:
        db.execute("SELECT pg_advisory_xact_lock(71820454)")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            version = path.stem
            if db.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (version,)).fetchone():
                continue
            db.execute(path.read_text())
            db.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))
