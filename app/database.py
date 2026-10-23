"""Silnik i fabryka sesji SQLAlchemy (synchroniczne, SQLite domyślnie)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """SQLite nie przechowuje strefy czasowej: zapisujemy UTC, odczytujemy jako aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Wymagany datetime ze strefą czasową.")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=timezone.utc)


def make_database(url: str) -> tuple[Engine, sessionmaker[Session]]:
    parsed = make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"
    in_memory = is_sqlite and parsed.database in (None, "", ":memory:")

    kwargs: dict = {}
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 5}
        if in_memory:
            kwargs["poolclass"] = StaticPool  # jedno połączenie współdzielone przez wątki

    engine = create_engine(url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")  # domyślnie wyłączone w SQLite
            if not in_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    # expire_on_commit=False: obiekty po commicie nadal czytelne (mapujemy je na Pydantic).
    return engine, sessionmaker(engine, expire_on_commit=False)