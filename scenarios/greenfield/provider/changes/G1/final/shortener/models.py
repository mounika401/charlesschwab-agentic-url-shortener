"""Domain objects. Plain dataclasses: no persistence or transport concerns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Link:
    code: str
    url: str
    created_at: datetime
    expires_at: datetime | None = None
    click_count: int = 0

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or utcnow()) >= self.expires_at
