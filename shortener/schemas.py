"""HTTP request/response schemas (the public API contract)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class CreateLinkRequest(BaseModel):
    url: str = Field(..., description="Absolute http(s) destination URL", examples=["https://example.com/docs"])
    custom_alias: str | None = Field(None, description="Optional vanity code, 3-32 chars [A-Za-z0-9_-]")
    ttl_seconds: int | None = Field(None, gt=0, le=60 * 60 * 24 * 365 * 5, description="Link lifetime in seconds")


class LinkResponse(BaseModel):
    code: str
    short_url: str
    url: str
    created_at: datetime
    expires_at: datetime | None
    click_count: int


class ErrorResponse(BaseModel):
    error: str
    detail: str


class DailyClicks(BaseModel):
    day: date
    clicks: int


class ReferrerCount(BaseModel):
    host: str
    clicks: int


class StatsResponse(BaseModel):
    code: str
    total_clicks: int
    unique_visitors: int
    clicks_by_day: list[DailyClicks]
    top_referrers: list[ReferrerCount]
