"""FastAPI application factory and HTTP routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .config import Settings
from .db import Database
from .errors import (
    AliasTakenError,
    CodeSpaceExhaustedError,
    InvalidAliasError,
    InvalidUrlError,
    LinkExpiredError,
    LinkNotFoundError,
    ShortenerError,
)
from .link_service import LinkService
from .models import Link
from .repository import LinkRepository
from .schemas import CreateLinkRequest, ErrorResponse, LinkResponse

logger = logging.getLogger("shortener")

ERROR_STATUS: dict[type[ShortenerError], int] = {
    InvalidUrlError: 422,
    InvalidAliasError: 422,
    AliasTakenError: 409,
    LinkNotFoundError: 404,
    LinkExpiredError: 410,
    CodeSpaceExhaustedError: 503,
}


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or Database(settings.database_path)
    db.migrate()
    service = LinkService(LinkRepository(db), settings)

    app = FastAPI(title="URL Shortener", version="1.0.0")
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
        return JSONResponse(status_code=status, content=body.model_dump())

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz() -> dict:
        db.query("SELECT 1")
        return {"status": "ready", "schema_version": db.schema_version()}

    @app.post("/api/v1/links", status_code=201, response_model=LinkResponse, tags=["links"])
    def create_link(body: CreateLinkRequest) -> LinkResponse:
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

    @app.get("/{code}", tags=["redirect"], responses={404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}})
    def redirect(code: str) -> RedirectResponse:
        link = service.resolve(code)
        return RedirectResponse(link.url, status_code=307)

    return app
