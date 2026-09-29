from datetime import datetime, timedelta, timezone

from shortener.db import MIGRATIONS, Database
from shortener.models import Link
from shortener.repository import LinkRepository


def test_migrations_are_idempotent():
    db = Database(":memory:")
    assert db.migrate() == [v for v, _, _ in MIGRATIONS]
    assert db.migrate() == []
    assert db.schema_version() == MIGRATIONS[-1][0]


def test_repository_round_trip_and_duplicate_rejection(db):
    repo = LinkRepository(db)
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    link = Link("abc1234", "https://example.com", created, created + timedelta(days=1))
    assert repo.insert(link) is True
    assert repo.insert(link) is False
    assert repo.get("abc1234") == link


def test_increment_clicks_is_atomic_across_threads(db):
    import threading

    repo = LinkRepository(db)
    repo.insert(Link("hot", "https://example.com", datetime.now(timezone.utc)))
    threads = [threading.Thread(target=lambda: [repo.increment_clicks("hot") for _ in range(50)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert repo.get("hot").click_count == 400

