"""Thin Postgres wrapper. One connection per call — matches the one-request-
per-invocation shape of a serverless function; no pool to manage across
cold starts. Neon/Supabase both scale-to-zero fine with this pattern.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._dsn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply_migrations(self, migrations_dir) -> list[str]:
        """Runs every migrations/*.sql file in filename order. Migrations must
        be idempotent (CREATE TABLE IF NOT EXISTS, etc.) since this re-runs
        the whole directory rather than tracking a version number — fine at
        this project's scale (PRD §1.2: single-tenant, no need for a
        migration-runner framework)."""
        applied = []
        for path in sorted(migrations_dir.glob("*.sql")):
            sql = path.read_text()
            with self.connection() as conn:
                conn.execute(sql)
            applied.append(path.name)
        return applied
