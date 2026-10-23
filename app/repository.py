"""Dostęp do bazy: jedna sesja i jedna transakcja na operację.

Kolejność w operacjach zmieniających: odczyt stanu → sprawdzenie reguły → zmiana → commit.
Wyjątek wewnątrz bloku `begin()` wycofuje transakcję, a sesja jest zawsze zamykana.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db_models import TicketRow
from app.models import (
    ALLOWED_TRANSITIONS,
    Ticket,
    TicketCreate,
    TicketPriority,
    TicketStatus,
    parse_ticket_id,
)

class TicketNotFound(LookupError):
    pass


class InvalidTransition(ValueError):
    """Naruszenie reguły przejść statusu (mapowane na 409)."""


class ConcurrentUpdate(ValueError):
    """Status zmienił się równolegle między odczytem a zapisem (mapowane na 409)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

class TicketRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def list(self) -> list[Ticket]:
        with self._sessions() as db:
            rows = db.scalars(select(TicketRow).order_by(TicketRow.seq)).all()
            return [Ticket.model_validate(row) for row in rows]

    def get(self, ticket_id: str) -> Ticket:
        seq = parse_ticket_id(ticket_id)
        with self._sessions() as db:
            row = db.get(TicketRow, seq) if seq is not None else None
            if row is None:
                raise TicketNotFound(ticket_id)
            return Ticket.model_validate(row)

    def create(self, data: TicketCreate) -> Ticket:
        now = _utcnow()
        with self._sessions.begin() as db:
            row = TicketRow(
                title=data.title,
                description=data.description,
                reporter=data.reporter,
                priority=data.priority,
                assignee=data.assignee,
                status=TicketStatus.new,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()  # nadaje seq, a więc i ID
            return Ticket.model_validate(row)

    def update_status(self, ticket_id: str, new_status: TicketStatus) -> Ticket:
        seq = parse_ticket_id(ticket_id)
        with self._sessions.begin() as db:
            row = db.get(TicketRow, seq) if seq is not None else None
            if row is None:
                raise TicketNotFound(ticket_id)
            current = row.status
            if new_status not in ALLOWED_TRANSITIONS[current]:
                raise InvalidTransition(
                    f"Nie można zmienić statusu z '{current.value}' "
                    f"na '{new_status.value}'."
                )
            # Zapis warunkowy (optymistyczna kontrola współbieżności): zmiana tylko,
            # jeśli status nadal jest taki, jak podczas sprawdzania reguły.
            result = db.execute(
                update(TicketRow)
                .where(TicketRow.seq == seq, TicketRow.status == current)
                .values(status=new_status, updated_at=_utcnow())
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise ConcurrentUpdate(
                    f"Status zgłoszenia '{ticket_id}' zmienił się równolegle; spróbuj ponownie."
                )
            db.refresh(row)
            return Ticket.model_validate(row)

    def seed_if_empty(self) -> bool:
        """Dane początkowe z LAB1 — tylko do pustej tabeli, nigdy nie nadpisują stanu."""
        now = _utcnow()
        with self._sessions.begin() as db:
            if db.scalar(select(func.count()).select_from(TicketRow)) != 0:
                return False
            db.add_all(
                [
                    TicketRow(
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
                    TicketRow(
                        title="Brak dostępu do VPN",
                        description="Użytkownik nie może połączyć się z VPN uczelnianym od wczoraj.",
                        reporter="anna.nowak@uczelnia.pl",
                        priority=TicketPriority.high,
                        assignee="admin.it@uczelnia.pl",
                        status=TicketStatus.resolved,
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            return True