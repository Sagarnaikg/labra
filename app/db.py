"""SQLite access: create the schema and seed the shared Labra field list.

Create the database from the command line:

    python -m app.db            # writes ./labra.db
    LABRA_DB_PATH=/tmp/x.db python -m app.db
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = os.environ.get("LABRA_DB_PATH", "labra.db")

# The six fields the service starts with. This is seed data only: after the
# first run the database is the single source of truth for the field list.
SEED_FIELDS: list[tuple[str, str, bool]] = [
    ("company_name", "Company name", True),
    ("website", "Website", True),
    ("country", "Country", True),
    ("expected_monthly_revenue", "Expected monthly revenue", True),
    ("target_close_date", "Target close date", True),
    ("primary_sales_rep_email", "Primary sales rep email", True),
]


class Database:
    """Thin wrapper around a SQLite file path."""

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_DB_PATH) -> None:
        self.path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection; commit on success, roll back on any error."""
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        """Create tables and seed the original fields. Safe to run repeatedly."""
        with self.connect() as conn:
            conn.executescript(SCHEMA_PATH.read_text())
            # INSERT OR IGNORE: re-running never overwrites a label or
            # required flag that was changed later through the API.
            conn.executemany(
                "INSERT OR IGNORE INTO labra_fields (name, label, required, sort_order) "
                "VALUES (?, ?, ?, ?)",
                [
                    (name, label, int(required), i)
                    for i, (name, label, required) in enumerate(SEED_FIELDS, start=1)
                ],
            )


if __name__ == "__main__":
    db = Database()
    db.init()
    print(f"Database ready at {Path(db.path).resolve()}")
