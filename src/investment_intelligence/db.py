"""Database connection and migration running.

Migrations are numbered, plain SQL, and forward only. There are no down
migrations: this is an append-only store whose value is that its history is
intact, and a rollback that drops a column drops facts. If a migration is
wrong, the fix is another migration.

Applied migrations are recorded in `schema_migrations` so re-running is a
no-op. That table is created here rather than in a migration, because it has
to exist before the first one can be recorded.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text        PRIMARY KEY,
    sha256      text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def dsn() -> str:
    """Connection string, from the environment. Never hardcoded."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Local development expects a Postgres "
            "instance; production is Neon (ADR 002)."
        )
    return url


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or dsn())


@dataclass(frozen=True)
class Migration:
    path: Path

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def sql(self) -> str:
        return self.path.read_text()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Migrations in filename order. The numeric prefix is the ordering."""
    return [Migration(p) for p in sorted(directory.glob("*.sql"))]


class MigrationDrift(Exception):
    """An already-applied migration file has changed on disk.

    This matters more than it looks. Editing an applied migration means the
    schema in front of you is not the schema that ran, and the next
    environment you deploy to will get something different. The fix is a new
    migration, never an edit to an old one.
    """


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the filenames applied."""
    applied: list[str] = []

    with conn.cursor() as cur:
        cur.execute(_BOOTSTRAP)
        cur.execute("SELECT filename, sha256 FROM schema_migrations")
        seen = dict(cur.fetchall())

    for migration in discover(directory):
        if migration.filename in seen:
            if seen[migration.filename] != migration.sha256:
                raise MigrationDrift(
                    f"{migration.filename} has changed since it was applied. "
                    "Write a new migration instead of editing this one."
                )
            continue

        # Each migration is its own transaction: a failure leaves the
        # preceding ones applied and recorded, so a re-run resumes.
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(migration.sql)
            cur.execute(
                "INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)",
                (migration.filename, migration.sha256),
            )
        applied.append(migration.filename)

    return applied
