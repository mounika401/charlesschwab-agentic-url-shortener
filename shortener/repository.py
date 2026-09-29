"""Persistence for links. Pure data access: no validation or business rules."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from .db import Database
from .models import Link


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_link(row: sqlite3.Row) -> Link:
    return Link(
        code=row["code"],
        url=row["url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=_from_iso(row["expires_at"]),
        click_count=int(row["click_count"]),
    )


class LinkRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, link: Link) -> bool:
        """Insert a link; return False if the code already exists."""
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO links (code, url, created_at, expires_at, click_count)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (link.code, link.url, _to_iso(link.created_at), _to_iso(link.expires_at), link.click_count),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def get(self, code: str) -> Link | None:
        rows = self._db.query("SELECT * FROM links WHERE code = ?", (code,))
        return _row_to_link(rows[0]) if rows else None

    def delete(self, code: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM links WHERE code = ?", (code,))
            return cursor.rowcount > 0

    def increment_clicks(self, code: str) -> None:
        # Atomic in SQL: no read-modify-write race under concurrent redirects.
        with self._db.transaction() as conn:
            conn.execute("UPDATE links SET click_count = click_count + 1 WHERE code = ?", (code,))
