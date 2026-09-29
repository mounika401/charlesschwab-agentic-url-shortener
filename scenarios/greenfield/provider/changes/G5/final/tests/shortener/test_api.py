def create(client, **body):
    return client.post("/api/v1/links", json={"url": "https://example.com/a", **body})


def test_create_and_redirect(client):
    response = create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["short_url"] == f"http://sho.rt/{body['code']}"
    assert body["url"] == "https://example.com/a"

    redirect = client.get(f"/{body['code']}")
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://example.com/a"


def test_redirect_increments_click_count(client):
    code = create(client).json()["code"]
    client.get(f"/{code}")
    client.get(f"/{code}")
    assert client.get(f"/api/v1/links/{code}").json()["click_count"] == 2


def test_custom_alias_and_conflict(client):
    assert create(client, custom_alias="docs-link").status_code == 201
    conflict = create(client, custom_alias="docs-link")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "AliasTakenError"


def test_invalid_alias_and_url_are_422(client):
    assert create(client, custom_alias="api").status_code == 422
    assert client.post("/api/v1/links", json={"url": "javascript:alert(1)"}).status_code == 422


def test_ttl_must_be_positive(client):
    assert create(client, ttl_seconds=0).status_code == 422


def test_expired_link_returns_410(client, monkeypatch):
    from datetime import timedelta

    from shortener import models

    code = create(client, ttl_seconds=60).json()["code"]
    real_now = models.utcnow
    monkeypatch.setattr(models, "utcnow", lambda: real_now() + timedelta(seconds=61))
    assert client.get(f"/{code}").status_code == 410


def test_unknown_code_is_404(client):
    assert client.get("/nope123").status_code == 404
    assert client.get("/api/v1/links/nope123").status_code == 404


def test_delete(client):
    code = create(client).json()["code"]
    assert client.delete(f"/api/v1/links/{code}").status_code == 204
    assert client.get(f"/{code}").status_code == 404
    assert client.delete(f"/api/v1/links/{code}").status_code == 404


def test_health_and_readiness(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["status"] == "ready"


def test_request_id_is_propagated(client):
    assert client.get("/healthz", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"
