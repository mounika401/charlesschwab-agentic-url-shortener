"""Destination safety policy for a public-facing shortener.

``validation.validate_url`` checks that a URL is *well-formed*; this module
decides whether it is *acceptable to publish behind our domain*. Checks are
purely syntactic (no DNS lookups): they are deterministic, fast and cannot be
used to make the service issue network requests. DNS-rebinding style attacks
therefore remain out of scope and are listed as a known limitation.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import UnsafeUrlError

INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


@dataclass(frozen=True)
class UrlSafetyPolicy:
    blocked_domains: tuple[str, ...] = ()
    own_host: str | None = None

    def check(self, url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()

        if parts.username is not None or parts.password is not None:
            # https://trusted.com@evil.com/ reads as trusted.com to humans.
            raise UnsafeUrlError("URLs with embedded credentials are not allowed")
        if host == "localhost" or host.endswith(INTERNAL_SUFFIXES):
            raise UnsafeUrlError("internal hostnames are not allowed")
        if self.own_host and host == self.own_host.lower():
            raise UnsafeUrlError("links to this shortener are not allowed (redirect loops)")
        self._check_ip_literal(host)
        for blocked in self.blocked_domains:
            blocked = blocked.lower().lstrip(".")
            if host == blocked or host.endswith("." + blocked):
                raise UnsafeUrlError(f"destination domain {blocked!r} is blocked")

    @staticmethod
    def _check_ip_literal(host: str) -> None:
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            return  # not an IP literal
        if not ip.is_global:
            raise UnsafeUrlError("private, loopback and reserved IP addresses are not allowed")
