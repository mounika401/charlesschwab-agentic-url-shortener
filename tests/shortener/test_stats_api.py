def test_stats_endpoint_reports_clicks_referrers_and_visitors(client):
    code = client.post("/api/v1/links", json={"url": "https://example.com"}).json()["code"]
    client.get(f"/{code}", headers={"referer": "https://t.co/abc", "user-agent": "a"})
    client.get(f"/{code}", headers={"referer": "https://t.co/def", "user-agent": "a"})
    client.get(f"/{code}", headers={"user-agent": "b"})

    body = client.get(f"/api/v1/links/{code}/stats").json()
    assert body["total_clicks"] == 3
    assert body["unique_visitors"] == 2
    assert sum(d["clicks"] for d in body["clicks_by_day"]) == 3
    assert body["top_referrers"] == [{"host": "t.co", "clicks": 2}]


def test_stats_for_unknown_code_is_404(client):
    assert client.get("/api/v1/links/missing/stats").status_code == 404


def test_stats_for_new_link_are_empty(client):
    code = client.post("/api/v1/links", json={"url": "https://example.com"}).json()["code"]
    body = client.get(f"/api/v1/links/{code}/stats").json()
    assert body == {"code": code, "total_clicks": 0, "unique_visitors": 0, "clicks_by_day": [], "top_referrers": []}
