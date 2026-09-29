"""Redirect rate limiting (added after the release-review change request)."""

from fastapi.testclient import TestClient

from shortener.app import create_app
from shortener.config import Settings
from shortener.db import Database


def make_client(**overrides) -> TestClient:
    settings = Settings(database_path=":memory:", base_url="http://sho.rt", **overrides)
    return TestClient(create_app(settings, Database(":memory:")), follow_redirects=False)


def test_redirects_are_rate_limited_per_client():
    client = make_client(redirect_rate_limit_per_minute=3)
    code = client.post("/api/v1/links", json={"url": "https://example.com"}).json()["code"]
    assert [client.get(f"/{code}").status_code for _ in range(4)] == [307, 307, 307, 429]


def test_rate_limited_redirects_are_not_counted_as_clicks():
    client = make_client(redirect_rate_limit_per_minute=1)
    code = client.post("/api/v1/links", json={"url": "https://example.com"}).json()["code"]
    client.get(f"/{code}"), client.get(f"/{code}")
    assert client.get(f"/api/v1/links/{code}").json()["click_count"] == 1


def test_management_api_is_not_affected_by_redirect_limit():
    client = make_client(redirect_rate_limit_per_minute=1)
    code = client.post("/api/v1/links", json={"url": "https://example.com"}).json()["code"]
    client.get(f"/{code}"), client.get(f"/{code}")
    assert client.get(f"/api/v1/links/{code}").status_code == 200
