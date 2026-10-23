"""Kontrakt API (Pydantic) i reguły domenowe IT Ticket Service.

Modele ORM (zapis danych) są w db_models.py — tutaj tylko to, co widzi klient.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

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
        TicketStatus.closed,
    },
    TicketStatus.closed: {TicketStatus.closed, TicketStatus.new},
}

_TICKET_ID_RE = re.compile(r"^TCK-(\d{4,18})$")


def format_ticket_id(seq: int) -> str:
    return f"TCK-{seq:04d}"


def parse_ticket_id(ticket_id: str) -> int:
    """Zwraca numer sekwencyjny albo None, gdy ID nie ma kanonicznej postaci TCK-0001."""
    match = _TICKET_ID_RE.fullmatch(ticket_id)
    if match is None:
        return None
    seq = int(match.group(1))
    if seq < 1 or format_ticket_id(seq) != ticket_id:
        return None
    return seq


class TicketCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    reporter: str = Field(min_length=1, max_length=80)
    priority: TicketPriority = Field(default=TicketPriority.medium)
    assignee: str | None = Field(default=None, max_length=80)


class StatusUpdate(BaseModel):
    status: TicketStatus


class Ticket(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    reporter: str
    priority: TicketPriority
    assignee: str | None
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
