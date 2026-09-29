"""Business rules for creating, resolving and deleting short links."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import codegen
from .analytics import AnalyticsRepository, ClickContext, LinkStats
from .config import Settings
from .errors import (
    AliasTakenError,
    CodeSpaceExhaustedError,
    InvalidAliasError,
    LinkExpiredError,
    LinkNotFoundError,
)
from .models import Link, utcnow
from .repository import LinkRepository
from .safety import UrlSafetyPolicy
from .validation import validate_url


class LinkService:
    def __init__(
        self,
        repository: LinkRepository,
        settings: Settings,
        analytics: AnalyticsRepository | None = None,
        safety: UrlSafetyPolicy | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._analytics = analytics
        self._safety = safety or UrlSafetyPolicy()

    def create(
        self,
        url: str,
        custom_alias: str | None = None,
        ttl_seconds: int | None = None,
        now: datetime | None = None,
    ) -> Link:
        normalised = validate_url(url, self._settings.max_url_length)
        self._safety.check(normalised)
        created_at = now or utcnow()
        expires_at = created_at + timedelta(seconds=ttl_seconds) if ttl_seconds else None

        if custom_alias is not None:
            if not codegen.is_valid_alias(custom_alias):
                raise InvalidAliasError(
                    "alias must be 3-32 chars of letters, digits, '-' or '_' and not reserved"
                )
            link = Link(custom_alias, normalised, created_at, expires_at)
            if not self._repo.insert(link):
                raise AliasTakenError(f"alias {custom_alias!r} is already in use")
            return link

        for _ in range(self._settings.max_code_allocation_attempts):
            link = Link(codegen.generate_code(self._settings.code_length), normalised, created_at, expires_at)
            if self._repo.insert(link):
                return link
        raise CodeSpaceExhaustedError("could not allocate a unique code; increase code length")

    def get(self, code: str) -> Link:
        link = self._repo.get(code)
        if link is None:
            raise LinkNotFoundError(code)
        return link

    def resolve(self, code: str, ctx: ClickContext | None = None, now: datetime | None = None) -> Link:
        """Return the destination for a redirect and record the click.

        Expiry is checked *before* any click is recorded (BUG-101): an expired
        link is a dead link and must not accumulate analytics.
        """
        link = self.get(code)
        if link.is_expired(now):
            raise LinkExpiredError(code)
        self._repo.increment_clicks(code)
        if self._analytics is not None:
            self._analytics.record(code, ctx or ClickContext(), now or utcnow())
        return link

    def delete(self, code: str) -> None:
        if not self._repo.delete(code):
            raise LinkNotFoundError(code)

    def stats(self, code: str) -> LinkStats:
        self.get(code)  # 404 for unknown codes rather than empty stats
        if self._analytics is None:
            raise RuntimeError("analytics is not configured")
        return self._analytics.stats(code)
