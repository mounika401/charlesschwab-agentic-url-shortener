from fastapi.testclient import TestClient

from shortener.app import create_app
from shortener.config import Settings
from shortener.db import Database


def make_client(**overrides) -> TestClient:
    settings = Settings(database_path=":memory:", base_url="http://sho.rt", **overrides)
    return TestClient(create_app(settings, Database(":memory:")), follow_redirects=False)


def test_unsafe_destination_is_rejected_with_422(client):
    response = client.post("/api/v1/links", json={"url": "http://169.254.169.254/latest"})
    assert response.status_code == 422
    assert response.json()["error"] == "UnsafeUrlError"


def test_blocked_domain_from_settings():
    client = make_client(blocked_domains=("evil.test",))
    assert client.post("/api/v1/links", json={"url": "https://a.evil.test/"}).status_code == 422
    assert client.post("/api/v1/links", json={"url": "https://good.test/"}).status_code == 201


def test_create_is_rate_limited_with_retry_after():
    client = make_client(create_rate_limit_per_minute=2)
    for _ in range(2):
        assert client.post("/api/v1/links", json={"url": "https://example.com"}).status_code == 201
    limited = client.post("/api/v1/links", json={"url": "https://example.com"})
    assert limited.status_code == 429
    assert limited.json()["error"] == "RateLimitedError"
    assert int(limited.headers["Retry-After"]) >= 1


def test_zero_disables_create_limit():
    client = make_client(create_rate_limit_per_minute=0)
    for _ in range(50):
        assert client.post("/api/v1/links", json={"url": "https://example.com"}).status_code == 201
