"""Click analytics: privacy-preserving event capture and aggregation.

Design notes
* Events are appended to ``click_events``; aggregates are computed on read.
  At prototype scale this is simpler and always consistent. At high volume the
  same interface can be backed by a stream + rollup table (see ADR-002).
* Visitors are identified by ``sha256(salt | ip | user-agent)`` truncated to 16
  hex chars. The salt is secret, so hashes cannot be reversed by brute-forcing
  the IPv4 space.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

from .db import Database


@dataclass(frozen=True)
class ClickContext:
    """What the transport layer knows about a redirect request."""

    client_ip: str | None = None
    user_agent: str | None = None
    referrer: str | None = None


@dataclass(frozen=True)
class LinkStats:
    code: str
    total_clicks: int
    unique_visitors: int
    clicks_by_day: list[tuple[date, int]] = field(default_factory=list)
    top_referrers: list[tuple[str, int]] = field(default_factory=list)


def visitor_hash(salt: str, ctx: ClickContext) -> str:
    material = f"{salt}|{ctx.client_ip or '-'}|{ctx.user_agent or '-'}".encode()
    return hashlib.sha256(material).hexdigest()[:16]


def referrer_host(referrer: str | None) -> str | None:
    if not referrer:
        return None
    host = urlsplit(referrer).hostname
    return host.lower() if host else None


class AnalyticsRepository:
    def __init__(self, db: Database, salt: str) -> None:
        self._db = db
        self._salt = salt

    def record(self, code: str, ctx: ClickContext, occurred_at: datetime) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO click_events (code, occurred_at, referrer_host, visitor_hash) VALUES (?, ?, ?, ?)",
                (code, occurred_at.isoformat(), referrer_host(ctx.referrer), visitor_hash(self._salt, ctx)),
            )

    def stats(self, code: str, top_n: int = 5, days: int = 30) -> LinkStats:
        totals = self._db.query(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT visitor_hash) AS uniq FROM click_events WHERE code = ?",
            (code,),
        )[0]
        by_day = self._db.query(
            "SELECT substr(occurred_at, 1, 10) AS day, COUNT(*) AS n FROM click_events"
            " WHERE code = ? GROUP BY day ORDER BY day DESC LIMIT ?",
            (code, days),
        )
        referrers = self._db.query(
            "SELECT referrer_host AS host, COUNT(*) AS n FROM click_events"
            " WHERE code = ? AND referrer_host IS NOT NULL GROUP BY host ORDER BY n DESC, host LIMIT ?",
            (code, top_n),
        )
        return LinkStats(
            code=code,
            total_clicks=int(totals["total"]),
            unique_visitors=int(totals["uniq"]),
            clicks_by_day=[(date.fromisoformat(r["day"]), int(r["n"])) for r in reversed(by_day)],
            top_referrers=[(r["host"], int(r["n"])) for r in referrers],
        )
