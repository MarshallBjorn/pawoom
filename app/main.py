"""IT Ticket Service — mikrousługa obsługująca zgłoszenia IT.

LAB2: dane w SQLite (SQLAlchemy), odczyt szczegółów zgłoszenia przez cache-aside w Redis.
Kontrakt API, identyfikatory i kody błędów są takie same jak w LAB1.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db_models  # noqa: F401  (rejestruje tabele w Base.metadata)
from app.cache import BYPASS, HIT, MISS, JsonCache, make_redis
from app.database import Base, make_database
from app.models import (
    SERVICE_NAME,
    StatusUpdate,
    Ticket,
    TicketCreate,
    parse_ticket_id,
)
from app.repository import TicketNotFound, TicketRepository

logger = logging.getLogger("app.main")

DEFAULT_DATABASE_URL = "sqlite:///./app.db"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_CACHE_PREFIX = f"lab2:{SERVICE_NAME}"
DEFAULT_CACHE_TTL_SECONDS = 60


def _not_found(ticket_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Zgłoszenie o ID '{ticket_id}' nie istnieje.")


def create_app(
    database_url: str | None = None,
    redis_client: Redis | None = None,
) -> FastAPI:
    """Fabryka aplikacji. Argumenty pozwalają testom wskazać osobną bazę i atrapę Redis.

    Zasoby (silnik, klient Redis) powstają w lifespan i są zamykane przy zatrzymaniu;
    aplikacja przejmuje odpowiedzialność za zamknięcie także wstrzykniętego klienta.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, sessions = make_database(
            database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        )
        client = None
        try:
            client = (
                redis_client
                if redis_client is not None
                else make_redis(os.getenv("REDIS_URL", DEFAULT_REDIS_URL))
            )

            Base.metadata.create_all(engine)
            repository = TicketRepository(sessions)
            repository.seed_if_empty()
            app.state.engine = engine
            app.state.redis = client
            app.state.repository = repository
            app.state.cache = JsonCache(
                client,
                prefix=os.getenv("CACHE_PREFIX", DEFAULT_CACHE_PREFIX),
                ttl=int(os.getenv("CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)),
                resource_name="ticket",
            )
            yield
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                engine.dispose()

    app = FastAPI(
        title=SERVICE_NAME, 
        description="Mikrousługa obsługująca zgłoszenia IT (LAB2 — PAWOoM).",
        lifespan=lifespan
    )

    def get_repository(request: Request) -> TicketRepository:
        return request.app.state.repository

    def get_cache(request: Request) -> JsonCache:
        return request.app.state.cache

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/ready")
    def ready(request: Request) -> JSONResponse:
        """Gotowość: baza jest wymagana (503), cache opcjonalny (degraded, 200)."""
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            db_ok = True
        except SQLAlchemyError:
            db_ok = False
        try:
            cache_ok = bool(request.app.state.redis.ping())
        except RedisError:
            cache_ok = False
        status = "ok" if (db_ok and cache_ok) else ("degraded" if db_ok else "error")
        return JSONResponse(
            status_code=200 if db_ok else 503,
            content={
                "service": SERVICE_NAME,
                "status": status,
                "database": "ok" if db_ok else "down",
                "cache": "ok" if cache_ok else "down",
            },
        )

    @app.get("/tickets", response_model=list[Ticket])
    def list_tickets(repo: TicketRepository = Depends(get_repository)) -> list[Ticket]:
        return repo.list()

    @app.get("/tickets/{ticket_id}", response_model=Ticket)
    def get_ticket(
        ticket_id: str,
        response: Response,
        repo: TicketRepository = Depends(get_repository),
        cache: JsonCache = Depends(get_cache),
    ) -> dict:
        # ID spoza formatu TCK-0001 nie istnieje — nie dotykamy ani cache, ani bazy.
        if parse_ticket_id(ticket_id) is None:
            raise _not_found(ticket_id)

        cached, source = cache.get(ticket_id)
        if cached is not None:
            try:
                Ticket.model_validate(cached)
            except ValidationError:
                logger.warning("cache: wpis %s niezgodny ze schematem — MISS", ticket_id)
                cache.delete(ticket_id)
                cached, source = None, MISS
        if cached is not None:
            response.headers["X-Cache"] = HIT
            return cached

        try:
            ticket = repo.get(ticket_id)
        except TicketNotFound as exc:
            raise _not_found(ticket_id) from exc  # 404 nie trafia do cache

        payload = ticket.model_dump(mode="json")
        if source != BYPASS and not cache.put(ticket_id, payload):
            source = BYPASS
        response.headers["X-Cache"] = source
        return payload

    @app.post("/tickets", response_model=Ticket, status_code=201)
    def create_ticket(
        data: TicketCreate, repo: TicketRepository = Depends(get_repository)
    ) -> Ticket:
        ticket = repo.create(data)
        return ticket

    @app.patch("/tickets/{ticket_id}/status", response_model=Ticket)
    def update_ticket_status(
        ticket_id: str,
        data: StatusUpdate,
        repo: TicketRepository = Depends(get_repository),
        cache: JsonCache = Depends(get_cache),
    ) -> Ticket:
        # 1. Reguła + zapis + COMMIT w bazie (wyjątek = rollback, cache nietknięty).
        try:
            updated = repo.update_status(ticket_id, data.status)
        except TicketNotFound as exc:
            raise _not_found(ticket_id) from exc
        except ValueError as exc:  # InvalidTransition, ConcurrentUpdate
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # 2. Unieważnienie dopiero po commicie; awaria Redis nie cofa zapisu.
        cache.delete(ticket_id)
        return updated

    return app

app = create_app()