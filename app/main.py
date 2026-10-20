"""IT Ticket Service — mikrousługa obsługująca zgłoszenia IT.

Dane przechowywane są w pamięci procesu (patrz TicketStore) i są tracone
przy restarcie serwera — zgodnie z zakresem LAB1.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from enum import Enum

from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel, Field

SERVICE_NAME = "it-ticket-service"


class TicketPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

class TicketStatus(str, Enum):
    new = "new"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"


ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.new: {TicketStatus.new, TicketStatus.in_progress},
    TicketStatus.in_progress: {TicketStatus.in_progress, TicketStatus.resolved},
    TicketStatus.resolved: {
        TicketStatus.resolved, 
        TicketStatus.in_progress,
        TicketStatus.closed
    },
    TicketStatus.closed: {TicketStatus.closed, TicketStatus.new},
}


class TicketCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    reporter: str = Field(min_length=1, max_length=80)
    priority: TicketPriority = Field(default=TicketPriority.medium)
    assignee: str | None = Field(default=None, max_length=80)

class StatusUpdate(BaseModel):
    status: TicketStatus


class Ticket(BaseModel):
    id: str
    title: str
    description: str
    reporter: str
    priority: TicketPriority
    assignee: str | None
    status: TicketStatus
    created_at: datetime
    updated_at: datetime

class TicketStore:
    """Magazyn zgłoszeń w pamięci — jedna instancja na jedną instancję aplikacji."""
    def __init__(self):
        self._tickets: dict[str, Ticket] = {}
        self._id_counter = itertools.count(1)
        self._seed()

    def _next_id(self) -> int:
        return f"TCK-{next(self._id_counter):04d}"

    def _seed(self) -> None:
        now = datetime.now(timezone.utc)
        for ticket in (
            Ticket(
                id=self._next_id(),
                title="Drukarka nie drukuje w sali 204",
                description=(
                    "Drukarka sieciowa zgłasza błąd offline po aktualizacji sterownika."
                ),
                reporter="jan.kowalski@uczelnia.pl",
                priority=TicketPriority.medium,
                assignee=None,
                status=TicketStatus.new,
                created_at=now,
                updated_at=now,
            ),
            Ticket(
                id=self._next_id(),
                title="Brak dostępu do VPN",
                description="Użytkownik nie może połączyć się z VPN uczelnianym od wczoraj.",
                reporter="anna.nowak@uczelnia.pl",
                priority=TicketPriority.high,
                assignee="admin.it@uczelnia.pl",
                status=TicketStatus.resolved,
                created_at=now,
                updated_at=now,
            ),
        ):
            self._tickets[ticket.id] = ticket

    def list_tickets(self) -> list[Ticket]:
        return list(self._tickets.values())

    def get(self, ticket_id: str) -> Ticket | None:
        return self._tickets.get(ticket_id)

    def create(self, data: TicketCreate) -> Ticket:
        now = datetime.now(timezone.utc)
        ticket = Ticket(
            id=self._next_id(),
            title=data.title,
            description=data.description,
            reporter=data.reporter,
            priority=data.priority,
            assignee=data.assignee,
            status=TicketStatus.new,
            created_at=now,
            updated_at=now,
        )
        self._tickets[ticket.id] = ticket
        return ticket

    def update_status(self, ticket_id: str, new_status: TicketStatus) -> Ticket:
        ticket = self._tickets[ticket_id]
        allowed = ALLOWED_TRANSITIONS[ticket.status]
        if new_status not in allowed:
            raise ValueError(
                f"Nie można zmienić statusu z '{ticket.status.value}' "
                f"na '{new_status.value}'."
            )
        updated = ticket.model_copy(
            update={"status": new_status, "updated_at": datetime.now(timezone.utc)}
        )
        self._tickets[ticket_id] = updated
        return updated


def create_app() -> FastAPI:
    app = FastAPI(
        title=SERVICE_NAME,
        description="Mikrousługa obsługująca zgłoszenia IT (helpdesk).",
    )
    app.state.store = TicketStore()

    def get_store(request: Request) -> TicketStore:
        return request.app.state.store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/tickets", response_model=list[Ticket])
    def list_tickets(store: TicketStore = Depends(get_store)) -> list[Ticket]:
        return store.list_tickets()

    @app.get("/tickets/{ticket_id}", response_model=Ticket)
    def get_ticket(ticket_id: str, store: TicketStore = Depends(get_store)) -> Ticket:
        ticket = store.get(ticket_id)
        if ticket is None:
            raise HTTPException(
                status_code=404, detail=f"Zgłoszenie o ID '{ticket_id}' nie istnieje."
            )
        return ticket

    @app.post("/tickets", response_model=Ticket, status_code=201)
    def create_ticket(
        data: TicketCreate, store: TicketStore = Depends(get_store)
    ) -> Ticket:
        return store.create(data)

    @app.patch("/tickets/{ticket_id}/status", response_model=Ticket)
    def update_ticket_status(
        ticket_id: str,
        data: StatusUpdate,
        store: TicketStore = Depends(get_store)
    ) -> Ticket:
        if store.get(ticket_id) is None:
            raise HTTPException(
                status_code=404, detail=f"Zgłoszenie o ID '{ticket_id}' nie istnieje."
            )
        try:
            return store.update_status(ticket_id, data.status)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    
    return app


app = create_app()


# TODO:
# 1. Choose your domain and name the service.
# 2. Add your domain models.
# 3. Add one list endpoint and one detail endpoint.
# 4. Add one create or update endpoint.
# 5. Add a business rule beyond types, required fields and unique identifiers.
# 6. Reject invalid operations before changing state; handle 404, 409 and 422.
# 7. Add at least three domain tests with fresh application state for each test.
# 8. Verify write/read and restart via HTTP; complete the README.
