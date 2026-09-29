from __future__ import annotations

import pytest

from shortener.config import Settings
from shortener.db import Database


@pytest.fixture
def settings() -> Settings:
    return Settings(database_path=":memory:", base_url="http://sho.rt")


@pytest.fixture
def db(settings: Settings):
    database = Database(settings.database_path)
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def client(settings: Settings, db: Database):
    # Imported lazily so persistence-layer tests can run before the HTTP layer exists.
    from fastapi.testclient import TestClient

    from shortener.app import create_app

    return TestClient(create_app(settings, db), follow_redirects=False)
