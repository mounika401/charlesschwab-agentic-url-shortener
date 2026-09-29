from datetime import datetime, timedelta, timezone

import pytest

from shortener.config import Settings
from shortener.errors import AliasTakenError, InvalidAliasError, LinkExpiredError, LinkNotFoundError
from shortener.link_service import LinkService
from shortener.models import Link
from shortener.repository import LinkRepository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def service(db):
    return LinkService(LinkRepository(db), Settings(database_path=":memory:"))


def test_code_collision_is_retried(db, monkeypatch):
    repo = LinkRepository(db)
    repo.insert(Link("taken00", "https://example.com", NOW))
    codes = iter(["taken00", "taken00", "fresh00"])
    monkeypatch.setattr("shortener.codegen.generate_code", lambda length: next(codes))
    link = LinkService(repo, Settings(database_path=":memory:")).create("https://example.org")
    assert link.code == "fresh00"


def test_ttl_sets_expiry_and_resolve_honours_it(service):
    link = service.create("https://example.com", ttl_seconds=60, now=NOW)
    assert link.expires_at == NOW + timedelta(seconds=60)
    assert service.resolve(link.code, now=NOW + timedelta(seconds=59)).url == "https://example.com"
    with pytest.raises(LinkExpiredError):
        service.resolve(link.code, now=NOW + timedelta(seconds=60))


def test_alias_rules(service):
    service.create("https://example.com", custom_alias="team-docs")
    with pytest.raises(AliasTakenError):
        service.create("https://example.org", custom_alias="team-docs")
    with pytest.raises(InvalidAliasError):
        service.create("https://example.org", custom_alias="admin")


def test_unknown_code(service):
    with pytest.raises(LinkNotFoundError):
        service.resolve("missing")
    with pytest.raises(LinkNotFoundError):
        service.delete("missing")
