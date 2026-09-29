from datetime import date, datetime, timezone

from shortener.analytics import AnalyticsRepository, ClickContext, referrer_host, visitor_hash
from shortener.models import Link
from shortener.repository import LinkRepository


def test_visitor_hash_is_salted_and_stable():
    ctx = ClickContext("203.0.113.9", "curl/8")
    assert visitor_hash("s1", ctx) == visitor_hash("s1", ctx)
    assert visitor_hash("s1", ctx) != visitor_hash("s2", ctx)
    assert "203.0.113.9" not in visitor_hash("s1", ctx)


def test_referrer_is_reduced_to_host():
    assert referrer_host("https://News.Example.com/a?b=c") == "news.example.com"
    assert referrer_host(None) is None
    assert referrer_host("not a url") is None


def test_stats_aggregation(db):
    LinkRepository(db).insert(Link("abc", "https://example.com", datetime.now(timezone.utc)))
    analytics = AnalyticsRepository(db, salt="test")
    day1 = datetime(2026, 3, 1, 10, tzinfo=timezone.utc)
    day2 = datetime(2026, 3, 2, 10, tzinfo=timezone.utc)
    alice = ClickContext("198.51.100.1", "firefox", "https://t.co/x")
    bob = ClickContext("198.51.100.2", "chrome", "https://news.example.com/")
    analytics.record("abc", alice, day1)
    analytics.record("abc", alice, day1)
    analytics.record("abc", bob, day2)
    analytics.record("abc", ClickContext(), day2)

    stats = analytics.stats("abc")
    assert stats.total_clicks == 4
    assert stats.unique_visitors == 3
    assert stats.clicks_by_day == [(date(2026, 3, 1), 2), (date(2026, 3, 2), 2)]
    assert stats.top_referrers == [("t.co", 2), ("news.example.com", 1)]


def test_events_are_deleted_with_link(db):
    repo = LinkRepository(db)
    repo.insert(Link("gone", "https://example.com", datetime.now(timezone.utc)))
    AnalyticsRepository(db, "s").record("gone", ClickContext(), datetime.now(timezone.utc))
    repo.delete("gone")
    assert db.query("SELECT COUNT(*) AS n FROM click_events")[0]["n"] == 0
