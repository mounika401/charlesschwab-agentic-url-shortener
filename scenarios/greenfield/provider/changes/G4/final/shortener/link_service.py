"""Business rules for creating, resolving and deleting short links."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import codegen
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
from .validation import validate_url


class LinkService:
    def __init__(self, repository: LinkRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings

    def create(
        self,
        url: str,
        custom_alias: str | None = None,
        ttl_seconds: int | None = None,
        now: datetime | None = None,
    ) -> Link:
        normalised = validate_url(url, self._settings.max_url_length)
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

    def resolve(self, code: str, now: datetime | None = None) -> Link:
        """Return the destination for a redirect and record the click."""
        link = self.get(code)
        self._repo.increment_clicks(code)
        if link.is_expired(now):
            raise LinkExpiredError(code)
        return link

    def delete(self, code: str) -> None:
        if not self._repo.delete(code):
            raise LinkNotFoundError(code)
