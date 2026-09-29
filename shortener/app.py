"""FastAPI application factory and HTTP routes."""

from __future__ import annotations

import logging
import math
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .analytics import AnalyticsRepository, ClickContext
from .config import Settings
from .db import Database
from .errors import (
    AliasTakenError,
    CodeSpaceExhaustedError,
    InvalidAliasError,
    InvalidUrlError,
    LinkExpiredError,
    LinkNotFoundError,
    RateLimitedError,
    ShortenerError,
    UnsafeUrlError,
)
from .link_service import LinkService
from .models import Link
from .ratelimit import TokenBucketLimiter
from .repository import LinkRepository
from .safety import UrlSafetyPolicy
from .schemas import (
    CreateLinkRequest,
    DailyClicks,
    ErrorResponse,
    LinkResponse,
    ReferrerCount,
    StatsResponse,
)

logger = logging.getLogger("shortener")

ERROR_STATUS: dict[type[ShortenerError], int] = {
    InvalidUrlError: 422,
    UnsafeUrlError: 422,
    InvalidAliasError: 422,
    AliasTakenError: 409,
    LinkNotFoundError: 404,
    LinkExpiredError: 410,
    CodeSpaceExhaustedError: 503,
    RateLimitedError: 429,
}


def _limiter(per_minute: int) -> TokenBucketLimiter | None:
    return TokenBucketLimiter(per_minute, 60.0) if per_minute > 0 else None


def _client_key(request: Request) -> str:
    # Direct peer address. Behind a proxy this must come from a trusted
    # X-Forwarded-For hop instead; trusting the header blindly lets clients
    # choose their own rate-limit bucket.
    return request.client.host if request.client else "unknown"


def _enforce(limiter: TokenBucketLimiter | None, request: Request) -> None:
    if limiter is None:
        return
    decision = limiter.acquire(_client_key(request))
    if not decision.allowed:
        raise RateLimitedError(decision.retry_after_seconds)


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or Database(settings.database_path)
    db.migrate()
    safety = UrlSafetyPolicy(settings.blocked_domains, urlsplit(settings.base_url).hostname)
    service = LinkService(
        LinkRepository(db), settings, AnalyticsRepository(db, settings.analytics_salt), safety
    )
    create_limiter = _limiter(settings.create_rate_limit_per_minute)
    redirect_limiter = _limiter(settings.redirect_rate_limit_per_minute)

    app = FastAPI(title="URL Shortener", version="1.2.0")
    app.state.settings = settings
    app.state.db = db
    app.state.service = service

    def to_response(link: Link) -> LinkResponse:
        return LinkResponse(
            code=link.code,
            short_url=f"{settings.base_url}/{link.code}",
            url=link.url,
            created_at=link.created_at,
            expires_at=link.expires_at,
            click_count=link.click_count,
        )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response

    @app.exception_handler(ShortenerError)
    async def domain_error(_: Request, exc: ShortenerError) -> JSONResponse:
        status = ERROR_STATUS.get(type(exc), 400)
        body = ErrorResponse(error=type(exc).__name__, detail=str(exc))
        headers = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(max(1, math.ceil(exc.retry_after_seconds)))
        return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> dict:
        db.query("SELECT 1")
        return {"status": "ready", "schema_version": db.schema_version()}

    @app.post(
        "/api/v1/links",
        status_code=201,
        response_model=LinkResponse,
        tags=["links"],
        responses={422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
    )
    def create_link(body: CreateLinkRequest, request: Request) -> LinkResponse:
        _enforce(create_limiter, request)
        link = service.create(body.url, body.custom_alias, body.ttl_seconds)
        logger.info("link.created code=%s", link.code)
        return to_response(link)

    @app.get("/api/v1/links/{code}", response_model=LinkResponse, tags=["links"])
    def get_link(code: str) -> LinkResponse:
        return to_response(service.get(code))

    @app.delete("/api/v1/links/{code}", status_code=204, tags=["links"])
    def delete_link(code: str) -> Response:
        service.delete(code)
        return Response(status_code=204)

    @app.get("/api/v1/links/{code}/stats", response_model=StatsResponse, tags=["analytics"])
    def link_stats(code: str) -> StatsResponse:
        stats = service.stats(code)
        return StatsResponse(
            code=stats.code,
            total_clicks=stats.total_clicks,
            unique_visitors=stats.unique_visitors,
            clicks_by_day=[DailyClicks(day=d, clicks=n) for d, n in stats.clicks_by_day],
            top_referrers=[ReferrerCount(host=h, clicks=n) for h, n in stats.top_referrers],
        )

    @app.get(
        "/{code}",
        tags=["redirect"],
        responses={404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
    )
    def redirect(code: str, request: Request) -> RedirectResponse:
        _enforce(redirect_limiter, request)
        ctx = ClickContext(
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            referrer=request.headers.get("referer"),
        )
        link = service.resolve(code, ctx)
        return RedirectResponse(link.url, status_code=307)

    return app
