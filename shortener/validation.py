"""Destination URL validation."""

from __future__ import annotations

from urllib.parse import urlsplit

from .errors import InvalidUrlError

ALLOWED_SCHEMES = frozenset({"http", "https"})


def validate_url(url: str, max_length: int = 2048) -> str:
    """Return the normalised URL or raise ``InvalidUrlError``.

    Only absolute http(s) URLs with a host are accepted. Rejecting other
    schemes (``javascript:``, ``data:``, ``file:``) prevents the shortener from
    being used to smuggle script or local-file URLs behind a trusted domain.
    """
    candidate = (url or "").strip()
    if not candidate:
        raise InvalidUrlError("url must not be empty")
    if len(candidate) > max_length:
        raise InvalidUrlError(f"url exceeds {max_length} characters")
    if any(ch.isspace() for ch in candidate):
        raise InvalidUrlError("url must not contain whitespace")
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrlError("only http and https URLs are allowed")
    if not parts.hostname:
        raise InvalidUrlError("url must include a host")
    # Lower-case scheme and host only; path and query are case-sensitive.
    userinfo, sep, hostport = parts.netloc.rpartition("@")
    return parts._replace(scheme=parts.scheme.lower(), netloc=f"{userinfo}{sep}{hostport.lower()}").geturl()
