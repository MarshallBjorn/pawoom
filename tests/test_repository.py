"""Repozytorium: rollback, współbieżność i zamykanie sesji."""

import pytest
from sqlalchemy import update

from app.database import Base, make_database
from app.db_models import TicketRow
from app.models import TicketCreate, TicketStatus
from app.repository import (
    ConcurrentUpdate,
    InvalidTransition,
    TicketNotFound,
    TicketRepository,
)


@pytest.fixture
def repo(tmp_path):
    engine, sessions = make_database("sqlite:///" + (tmp_path / "r.db").as_posix())
    Base.metadata.create_all(engine)
    repository = TicketRepository(sessions)
    repository.seed_if_empty()
    yield repository
    engine.dispose()


def test_rule_violation_raises_and_rolls_back(repo):
    before = repo.get("TCK-0001")
    with pytest.raises(InvalidTransition):
        repo.update_status("TCK-0001", TicketStatus.closed)
    assert repo.get("TCK-0001") == before


def test_missing_ticket_raises_lookup_error(repo):
    with pytest.raises(TicketNotFound):
        repo.update_status("TCK-0999", TicketStatus.new)
    with pytest.raises(LookupError):
        repo.get("nonsense")


def test_conditional_update_detects_concurrent_change(repo, monkeypatch):
    # Symulacja wyścigu: status zmienia się po sprawdzeniu reguły, przed UPDATE.
    original_execute = repo._sessions.class_.execute

    def racing_execute(self, statement, *args, **kwargs):
        if getattr(statement, "is_update", False) and not getattr(racing_execute, "done", False):
            racing_execute.done = True
            with repo._sessions() as other:
                other.execute(
                    update(TicketRow)
                    .where(TicketRow.seq == 1)
                    .values(status=TicketStatus.in_progress)
                )
                other.commit()
        return original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(repo._sessions.class_, "execute", racing_execute)
    with pytest.raises(ConcurrentUpdate):
        repo.update_status("TCK-0001", TicketStatus.in_progress)


def test_seed_is_idempotent(repo):
    assert repo.seed_if_empty() is False
    assert len(repo.list()) == 2


def test_create_assigns_sequential_id(repo):
    ticket = repo.create(TicketCreate(title="t", description="d", reporter="r"))
    assert ticket.id == "TCK-0003" and ticket.status == TicketStatus.new
