"""Trwałość w bazie, rollback i brak częściowego zapisu."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import MemoryRedis

NEW_TICKET = {
    "title": "Nie działa monitor",
    "description": "Brak sygnału.",
    "reporter": "a@x.pl",
}


def fresh_app(db_url):
    return create_app(database_url=db_url, redis_client=MemoryRedis())


def test_data_survives_restart_with_empty_cache(db_url):
    with TestClient(fresh_app(db_url)) as first:
        created = first.post("/tickets", json=NEW_TICKET).json()
        assert (
            first.patch(
                f"/tickets/{created['id']}/status", json={"status": "in_progress"}
            ).status_code
            == 200
        )

    with TestClient(fresh_app(db_url)) as second:  # nowy proces, pusty cache
        response = second.get(f"/tickets/{created['id']}")
        assert response.headers["X-Cache"] == "MISS"
        assert response.json()["title"] == NEW_TICKET["title"]
        assert response.json()["status"] == "in_progress"
        assert len(second.get("/tickets").json()) == 3  # seed nie dubluje się


def test_seed_does_not_overwrite_changed_state(db_url):
    with TestClient(fresh_app(db_url)) as first:
        first.patch("/tickets/TCK-0001/status", json={"status": "in_progress"})
    with TestClient(fresh_app(db_url)) as second:
        assert second.get("/tickets/TCK-0001").json()["status"] == "in_progress"


def test_ids_stay_sequential_across_restarts(db_url):
    with TestClient(fresh_app(db_url)) as first:
        assert first.post("/tickets", json=NEW_TICKET).json()["id"] == "TCK-0003"
    with TestClient(fresh_app(db_url)) as second:
        assert second.post("/tickets", json=NEW_TICKET).json()["id"] == "TCK-0004"


def test_rejected_transition_leaves_state_unchanged_after_restart(db_url):
    with TestClient(fresh_app(db_url)) as first:
        before = first.get("/tickets/TCK-0001").json()  # status: new
        rejected = first.patch("/tickets/TCK-0001/status", json={"status": "closed"})
        assert rejected.status_code == 409
    with TestClient(fresh_app(db_url)) as second:
        after = second.get("/tickets/TCK-0001").json()
    assert after == before  # także updated_at: nic nie zostało zapisane


def test_malformed_ids_are_404(client):
    for bad in ("1", "TCK-1", "tck-0001", "TCK-0000", "TCK-00001", "TCK-0001 "):
        assert client.get(f"/tickets/{bad}").status_code == 404
        assert client.patch(f"/tickets/{bad}/status", json={"status": "new"}).status_code == 404


def test_all_allowed_transitions(client):
    # new → in_progress → resolved → closed → new
    for target in ("in_progress", "resolved", "closed", "new"):
        response = client.patch("/tickets/TCK-0001/status", json={"status": target})
        assert response.status_code == 200 and response.json()["status"] == target


def test_timestamps_are_timezone_aware_and_updated(client):
    before = client.get("/tickets/TCK-0001").json()
    after = client.patch("/tickets/TCK-0001/status", json={"status": "in_progress"}).json()
    assert after["created_at"] == before["created_at"]
    assert after["updated_at"] > before["updated_at"]
    assert after["created_at"].endswith(("Z", "+00:00"))
