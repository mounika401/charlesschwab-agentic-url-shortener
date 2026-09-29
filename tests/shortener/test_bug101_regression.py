"""Regression test for BUG-101.

BUG-101: requesting an expired short link returned 410 but still incremented
``click_count``, inflating analytics for dead links. Written before the fix
(test-first) so the implementation gate can prove the fix.
"""

from datetime import timedelta

from shortener import models


def test_expired_link_does_not_count_clicks(client, monkeypatch):
    code = client.post("/api/v1/links", json={"url": "https://example.com", "ttl_seconds": 60}).json()["code"]
    real_now = models.utcnow
    monkeypatch.setattr(models, "utcnow", lambda: real_now() + timedelta(seconds=120))

    assert client.get(f"/{code}").status_code == 410
    assert client.get(f"/{code}").status_code == 410
    assert client.get(f"/api/v1/links/{code}").json()["click_count"] == 0


def test_expired_link_records_no_click_events(client, db, monkeypatch):
    code = client.post("/api/v1/links", json={"url": "https://example.com", "ttl_seconds": 60}).json()["code"]
    real_now = models.utcnow
    monkeypatch.setattr(models, "utcnow", lambda: real_now() + timedelta(seconds=120))

    client.get(f"/{code}")
    rows = db.query("SELECT COUNT(*) AS n FROM click_events WHERE code = ?", (code,))
    assert rows[0]["n"] == 0
