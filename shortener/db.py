"""SQLite connection management and forward-only, versioned migrations.

Migrations are append-only: an applied migration is never edited. Each one
runs inside a transaction and records its version in ``schema_version`` so a
restart never re-applies it.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "create links",
        """
        CREATE TABLE links (
            code        TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            expires_at  TEXT,
            click_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_links_created_at ON links (created_at);
        """,
    ),
    (
        2,
        "create click_events",
        # Raw IPs are never stored: visitor_hash is a salted, truncated digest and
        # the referrer is reduced to its host. ON DELETE CASCADE keeps analytics
        # from outliving the link they describe.
        """
        CREATE TABLE click_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            code          TEXT NOT NULL REFERENCES links (code) ON DELETE CASCADE,
            occurred_at   TEXT NOT NULL,
            referrer_host TEXT,
            visitor_hash  TEXT NOT NULL
        );
        CREATE INDEX idx_click_events_code_time ON click_events (code, occurred_at);
        """,
    ),
]


class Database:
    """Thread-safe wrapper around a single SQLite connection.

    SQLite serialises writes anyway; a single connection guarded by a lock is
    simpler and faster than a pool for this workload. ``:memory:`` is supported
    for tests.
    """

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def schema_version(self) -> int:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            return int(row["v"] or 0)

    def migrate(self) -> list[int]:
        """Apply pending migrations in order; return the versions applied."""
        applied: list[int] = []
        current = self.schema_version()
        for version, _name, sql in MIGRATIONS:
            if version <= current:
                continue
            with self.transaction() as conn:
                for statement in filter(None, (s.strip() for s in sql.split(";"))):
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            applied.append(version)
        return applied

    def close(self) -> None:
        with self._lock:
            self._conn.close()
