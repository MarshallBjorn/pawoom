"""Cache-aside: HIT/MISS, TTL, unieważnianie i awarie Redis."""

import json

from tests.conftest import PREFIX

KEY = f"{PREFIX}:ticket:TCK-0001"


def count_repo_gets(app, monkeypatch):
    calls = []
    original = app.state.repository.get

    def counting_get(ticket_id):
        calls.append(ticket_id)
        return original(ticket_id)

    monkeypatch.setattr(app.state.repository, "get", counting_get)
    return calls


def test_miss_then_hit_skips_repository(client, app, monkeypatch):
    calls = count_repo_gets(app, monkeypatch)
    first = client.get("/tickets/TCK-0001")
    second = client.get("/tickets/TCK-0001")
    assert (first.headers["X-Cache"], second.headers["X-Cache"]) == ("MISS", "HIT")
    assert first.json() == second.json()
    assert calls == ["TCK-0001"]  # przy HIT baza nie była odpytana


def test_entry_has_ttl_and_is_plain_json(client, fake_redis):
    client.get("/tickets/TCK-0001")
    assert fake_redis.ttl(KEY) == 60
    cached = json.loads(fake_redis.get(KEY))
    assert cached["id"] == "TCK-0001" and cached["status"] == "new"


def test_ttl_expiry_returns_to_miss(client, fake_redis):
    client.get("/tickets/TCK-0001")
    fake_redis.advance(59)
    assert client.get("/tickets/TCK-0001").headers["X-Cache"] == "HIT"
    fake_redis.advance(2)
    assert client.get("/tickets/TCK-0001").headers["X-Cache"] == "MISS"


def test_update_invalidates_cache(client, fake_redis):
    assert client.get("/tickets/TCK-0001").json()["status"] == "new"
    assert client.get("/tickets/TCK-0001").headers["X-Cache"] == "HIT"
    assert (
        client.patch("/tickets/TCK-0001/status", json={"status": "in_progress"}).status_code == 200
    )
    assert fake_redis.ttl(KEY) == -2
    after = client.get("/tickets/TCK-0001")
    assert after.headers["X-Cache"] == "MISS"
    assert after.json()["status"] == "in_progress"
    assert client.get("/tickets/TCK-0001").headers["X-Cache"] == "HIT"


def test_rejected_update_keeps_cache_entry(client, fake_redis):
    client.get("/tickets/TCK-0001")
    assert client.patch("/tickets/TCK-0001/status", json={"status": "closed"}).status_code == 409
    assert fake_redis.ttl(KEY) > 0
    assert client.get("/tickets/TCK-0001").headers["X-Cache"] == "HIT"


def test_404_is_not_cached(client, fake_redis):
    assert client.get("/tickets/TCK-0999").status_code == 404
    assert fake_redis.store == {}


def test_redis_down_read_and_write_work_on_database(client, fake_redis):
    fake_redis.down = True
    read = client.get("/tickets/TCK-0001")
    assert read.status_code == 200 and read.headers["X-Cache"] == "BYPASS"
    write = client.patch("/tickets/TCK-0001/status", json={"status": "in_progress"})
    assert write.status_code == 200
    fake_redis.down = False
    assert client.get("/tickets/TCK-0001").json()["status"] == "in_progress"


def test_redis_down_create_and_list_work(client, fake_redis):
    fake_redis.down = True
    body = {"title": "t", "description": "d", "reporter": "r"}
    assert client.post("/tickets", json=body).status_code == 201
    assert len(client.get("/tickets").json()) == 3


def test_failed_invalidation_leaves_stale_entry_until_ttl(client, fake_redis):
    """Znane ograniczenie: commit w bazie i DEL w Redis nie są jedną transakcją."""
    client.get("/tickets/TCK-0001")  # wypełnia cache: status new
    fake_redis.down = True
    assert (
        client.patch("/tickets/TCK-0001/status", json={"status": "in_progress"}).status_code == 200
    )
    fake_redis.down = False
    stale = client.get("/tickets/TCK-0001")
    assert stale.headers["X-Cache"] == "HIT" and stale.json()["status"] == "new"
    fake_redis.advance(61)  # po TTL wraca prawda z bazy
    fresh = client.get("/tickets/TCK-0001")
    assert fresh.headers["X-Cache"] == "MISS" and fresh.json()["status"] == "in_progress"


def test_corrupted_or_foreign_entry_is_treated_as_miss(client, fake_redis):
    fake_redis.set(KEY, "{not json")
    assert client.get("/tickets/TCK-0001").headers["X-Cache"] == "MISS"
    fake_redis.set(KEY, json.dumps({"id": "TCK-0001"}))  # niezgodne ze schematem
    response = client.get("/tickets/TCK-0001")
    assert response.headers["X-Cache"] == "MISS" and response.json()["title"]


def test_failure_between_get_and_put_reports_bypass(client, fake_redis, monkeypatch):
    monkeypatch.setattr(
        fake_redis,
        "set",
        lambda *a, **k: (_ for _ in ()).throw(
            __import__("redis").exceptions.TimeoutError("timeout")
        ),
    )
    response = client.get("/tickets/TCK-0001")
    assert response.status_code == 200 and response.headers["X-Cache"] == "BYPASS"


def test_ready_ok_degraded_and_error(client, app, fake_redis, monkeypatch):
    assert client.get("/ready").json() == {
        "service": "it-ticket-service",
        "status": "ok",
        "database": "ok",
        "cache": "ok",
    }
    fake_redis.down = True
    degraded = client.get("/ready")
    assert degraded.status_code == 200 and degraded.json()["status"] == "degraded"
    app.state.engine.dispose()
    monkeypatch.setattr(
        app.state.engine,
        "connect",
        lambda: (_ for _ in ()).throw(
            __import__("sqlalchemy").exc.OperationalError("SELECT 1", {}, Exception("down"))
        ),
    )
    error = client.get("/ready")
    assert error.status_code == 503 and error.json()["database"] == "down"
