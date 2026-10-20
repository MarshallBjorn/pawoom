"""Testy automatyczne IT Ticket Service.

Każdy test startuje z własnej, świeżej instancji aplikacji (create_app()),
więc testy nie zależą od siebie ani od kolejności wykonania.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


def test_health_returns_ok_and_service_name(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "it-ticket-service"


def test_list_and_get_known_ticket(client: TestClient) -> None:
    listed = client.get("/tickets")
    assert listed.status_code == 200
    tickets = listed.json()
    assert len(tickets) == 2
    known_id = tickets[0]["id"]

    detail = client.get(f"/tickets/{known_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == known_id
    assert detail.json()["title"] == tickets[0]["title"]


def test_create_ticket_then_read_it_back(client: TestClient) -> None:
    payload = {
        "title": "Nie działa monitor w sali 101",
        "description": "Monitor nie wykrywa sygnału z komputera dydaktycznego.",
        "reporter": "piotr.zielinski@uczelnia.pl",
        "priority": "low",
    }

    created = client.post("/tickets", json=payload)
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["status"] == "new"
    ticket_id = created_body["id"]

    fetched = client.get(f"/tickets/{ticket_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == payload["title"]
    assert fetched.json()["reporter"] == payload["reporter"]


def test_create_ticket_with_invalid_data_returns_422_and_no_state_change(
    client: TestClient,
) -> None:
    before = len(client.get("/tickets").json())

    response = client.post(
        "/tickets",
        json={
            "title": "",  # title nie może być puste
            "description": "Opis jest prawidłowy.",
            "reporter": "test@uczelnia.pl",
        },
    )
    assert response.status_code == 422

    after = len(client.get("/tickets").json())
    assert after == before


def test_get_missing_ticket_returns_404(client: TestClient) -> None:
    response = client.get("/tickets/UNKNOWN")
    assert response.status_code == 404
    assert "UNKNOWN" in response.json()["detail"]


def test_valid_status_transition_is_applied(client: TestClient) -> None:
    tickets = client.get("/tickets").json()
    new_ticket = next(t for t in tickets if t["status"] == "new")

    response = client.patch(
        f"/tickets/{new_ticket['id']}/status", json={"status": "in_progress"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


def test_business_rule_blocks_closed_to_in_progress_without_state_change(
    client: TestClient,
) -> None:
    tickets = client.get("/tickets").json()
    resolved_ticket = next(t for t in tickets if t["status"] == "resolved")

    closed = client.patch(
        f"/tickets/{resolved_ticket['id']}/status", json={"status": "closed"}
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    forbidden = client.patch(
        f"/tickets/{resolved_ticket['id']}/status", json={"status": "in_progress"}
    )
    assert forbidden.status_code == 409
    assert "closed" in forbidden.json()["detail"]

    unchanged = client.get(f"/tickets/{resolved_ticket['id']}")
    assert unchanged.json()["status"] == "closed"


def test_unknown_status_value_returns_422(client: TestClient) -> None:
    tickets = client.get("/tickets").json()
    ticket_id = tickets[0]["id"]

    response = client.patch(
        f"/tickets/{ticket_id}/status", json={"status": "archived"}
    )
    assert response.status_code == 422


def test_status_update_on_missing_ticket_returns_404(client: TestClient) -> None:
    response = client.patch("/tickets/UNKNOWN/status", json={"status": "new"})
    assert response.status_code == 404