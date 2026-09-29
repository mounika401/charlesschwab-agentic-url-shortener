"""Short-code generation.

Codes are random base62 strings from a CSPRNG. Random (rather than sequential)
codes do not leak creation volume and cannot be enumerated. With 7 characters
the space is 62**7 ~= 3.5e12, so collisions are rare; callers still retry a
bounded number of times on collision.
"""

from __future__ import annotations

import re
import secrets
import string

ALPHABET = string.ascii_letters + string.digits
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
RESERVED_ALIASES = frozenset({"api", "healthz", "readyz", "docs", "openapi.json", "redoc", "admin"})


def generate_code(length: int = 7) -> str:
    if length < 4:
        raise ValueError("code length must be at least 4")
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def is_valid_alias(alias: str) -> bool:
    return bool(ALIAS_PATTERN.match(alias)) and alias.lower() not in RESERVED_ALIASES
