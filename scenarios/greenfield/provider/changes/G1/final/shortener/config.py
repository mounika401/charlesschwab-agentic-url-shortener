"""Runtime configuration, read once from environment variables.

Keeping configuration in a single frozen object makes it explicit what the
service depends on and lets tests build isolated instances without patching
globals.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # fail fast on misconfiguration
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    database_path: str = "shortener.db"
    base_url: str = "http://localhost:8000"
    code_length: int = 7
    max_url_length: int = 2048
    max_code_allocation_attempts: int = 5

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.environ.get("SHORTENER_DB_PATH", cls.database_path),
            base_url=os.environ.get("SHORTENER_BASE_URL", cls.base_url).rstrip("/"),
            code_length=_int_env("SHORTENER_CODE_LENGTH", cls.code_length),
            max_url_length=_int_env("SHORTENER_MAX_URL_LENGTH", cls.max_url_length),
            max_code_allocation_attempts=_int_env(
                "SHORTENER_MAX_CODE_ATTEMPTS", cls.max_code_allocation_attempts
            ),
        )
