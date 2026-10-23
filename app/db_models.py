"""Modele ORM — opis zapisu danych (kontrakt API jest w models.py)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime
from app.models import TicketPriority, TicketStatus, format_ticket_id


def _enum(enum_cls: type, name: str) -> Enum:
    # native_enum=False + create_constraint=True: CHECK w bazie chroni przed nieznaną wartością.
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [member.value for member in e],
    )


class TicketRow(Base):
    __tablename__ = "tickets"
    # AUTOINCREMENT: numer nigdy nie zostanie użyty ponownie, nawet po usunięciu wiersza.
    __table_args__ = {"sqlite_autoincrement": True}

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000))
    reporter: Mapped[str] = mapped_column(String(80))
    priority: Mapped[TicketPriority] = mapped_column(_enum(TicketPriority, "ticket_priority"))
    assignee: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[TicketStatus] = mapped_column(
        _enum(TicketStatus, "ticket_status"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    @property
    def id(self) -> str:
        """Publiczny identyfikator TCK-0001 (typ i format bez zmian względem LAB1)."""
        return format_ticket_id(self.seq)