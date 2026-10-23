import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import MemoryRedis

PREFIX = "lab2:test:it-ticket-service"


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch):
    monkeypatch.setenv("CACHE_PREFIX", PREFIX)
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")


@pytest.fixture
def db_url(tmp_path):
    return "sqlite:///" + (tmp_path / "test.db").as_posix()


@pytest.fixture
def fake_redis():
    return MemoryRedis()


@pytest.fixture
def app(db_url, fake_redis):
    return create_app(database_url=db_url, redis_client=fake_redis)


@pytest.fixture
def client(app):
    # `with` uruchamia lifespan (baza, seed, cache) i sprząta po teście.
    with TestClient(app) as test_client:
        yield test_client
