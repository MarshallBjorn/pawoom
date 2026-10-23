# IT Ticket Service — LAB1 + LAB2

Mikrousługa do rejestrowania i obsługi zgłoszeń IT (helpdesk). LAB1: API i reguła biznesowa. LAB2 (sekcje 8–13): trwała baza SQLite, cache-aside w Redis, testy awarii.

## Uruchomienie i testy

Wymagania: Python 3.12+, [uv](https://docs.astral.sh/uv/), Redis (tylko do uruchomienia usługi; testy używają atrapy `MemoryRedis`). Komendy działają w PowerShellu i bashu; terminal w katalogu z tym README.

    uv sync                                   # tworzy .venv wg uv.lock
    uv run pytest -q                          # testy (bez Dockera i Redis)
    uv run ruff check .                       # lint

Redis (jedna z opcji, szczegóły w sekcji 8):

    docker run --name lab2-redis -p 127.0.0.1:6379:6379 -d redis:7-alpine
    docker exec lab2-redis redis-cli ping     # PONG

Usługa (PowerShell: `$env:NAZWA = 'wartość'`, bash: `export NAZWA=wartość`):

    $env:DATABASE_URL = 'sqlite:///./app.db'
    $env:REDIS_URL = 'redis://localhost:6379/0'
    $env:CACHE_PREFIX = 'lab2:12345:it-ticket-service'   # wpisz swój numer albumu
    $env:CACHE_TTL_SECONDS = '60'
    uv run uvicorn app.main:app --reload --port 8101

Zmienne czytane są przy starcie — po zmianie zatrzymaj Uvicorn (`Ctrl+C`) i uruchom go ponownie (`--reload` ich nie odświeża). Wszystkie mają wartości domyślne (patrz sekcja 8), więc `uv run uvicorn app.main:app --port 8101` też wystartuje. Swagger UI: `http://localhost:8101/docs`. `requirements.txt` jest generowany z `uv.lock`: `uv export --no-hashes --no-emit-project --no-annotate -o requirements.txt`.

## 1. Problem i granice

Zgłoszenia IT (awarie sprzętu, dostępu, oprogramowania) trzeba rejestrować, śledzić i kontrolować ich status, żeby nie ginęły i nie przechodziły w nielogiczne stany (np. zamknięte → w realizacji).

Usługa odpowiada za: rejestrowanie zgłoszeń, przechowywanie ich danych, kontrolę przejść statusu.

Usługa nie odpowiada za: konta/uprawnienia użytkowników, wysyłkę powiadomień, naprawę/transport sprzętu.

Pojęcia domenowe:
- Ticket — zgłoszenie IT.
- Reporter — osoba zgłaszająca.
- Assignee — opiekun ze strony IT.
- Priority — low / medium / high.
- Status — new / in_progress / resolved / closed.

| Planowana usługa | Odpowiedzialność | Własne dane | Operacje | Potrzebne informacje od innej usługi |
|---|---|---|---|---|
| IT Ticket Service — implementowana teraz | cykl życia zgłoszenia IT | Ticket (id, title, description, reporter, assignee, priority, status, created_at, updated_at) | list, get, create, change status | — |
| Notification Service — implementowana później | wysyłka powiadomień o zmianach zgłoszeń | kolejka/historia wysłanych powiadomień | przyjęcie zdarzenia, wysyłka | ticket_id, adresat, treść zdarzenia — od Ticket Service |

Granica: dane zgłoszenia zapisuje i zmienia wyłącznie Ticket Service; Notification Service nigdy nie modyfikuje ticketu, tylko reaguje na zdarzenie.

Przyszłe wywołanie HTTP (niezaimplementowane na LAB1):

    Nadawca: IT Ticket Service   Odbiorca: Notification Service
    POST /notifications
    Request:  {"ticket_id": "TCK-0001", "event": "status_changed", "to": "user@uczelnia.pl", "message": "Status zmienił się na in_progress"}
    Response: 202 {"notification_id": "NTF-0001", "status": "queued"}
    Niedostępność odbiorcy: operacja na tickecie kończy się sukcesem niezależnie (fire-and-forget, timeout + log).

## 2. Model i reguła biznesowa

Ticket: `id` (nadawany przez serwer, np. `TCK-0001`), `title` (wymagane, 1–120), `description` (wymagane, 1–2000), `reporter` (wymagane, 1–80), `priority` (enum: low/medium/high, domyślnie medium), `assignee` (opcjonalne, do 80), `status` (enum: new/in_progress/resolved/closed, zawsze `new` przy tworzeniu), `created_at`, `updated_at`.

Reguła biznesowa: dozwolone przejścia statusu — `new→in_progress`, `in_progress→resolved`, `resolved→in_progress`, `resolved→closed`, `closed→new`. Ustawienie tego samego statusu — no-op. Wszystko inne jest odrzucane przed zapisem.

- Dozwolona: `PATCH /tickets/TCK-0001/status {"status":"in_progress"}` gdy status = `new` → `200`, status zmienia się.
- Niedozwolona: `PATCH /tickets/TCK-0002/status {"status":"in_progress"}` gdy status = `closed` → `409`, status pozostaje `closed`.

## 3. Kontrakt API

| Metoda | Ścieżka | Znaczenie | Sukces | Błędy |
|---|---|---|---|---|
| GET | `/health` | Status i nazwa usługi | 200 | — |
| GET | `/ready` | *(LAB2, rozszerzenie)* gotowość: baza + cache | 200 (`ok`/`degraded`) | 503 (baza niedostępna) |
| GET | `/tickets` | Lista zgłoszeń | 200 | — |
| GET | `/tickets/{id}` | Szczegóły zgłoszenia (cache-aside, nagłówek `X-Cache`) | 200 | 404 |
| POST | `/tickets` | Utworzenie zgłoszenia | 201 | 422 |
| PATCH | `/tickets/{id}/status` | Zmiana statusu | 200 | 404, 409, 422 |

Przykład utworzenia:

    POST /tickets
    {"title":"Brak sieci","description":"Brak internetu w sali 5","reporter":"a@x.pl","priority":"high"}

    201
    {"id":"TCK-0003","title":"Brak sieci","description":"Brak internetu w sali 5","reporter":"a@x.pl","priority":"high","assignee":null,"status":"new","created_at":"...","updated_at":"..."}

Przykład zmiany statusu (dane początkowe: `TCK-0002` ma status `resolved`):

    PATCH /tickets/TCK-0002/status
    {"status":"closed"}

    200
    {"id":"TCK-0002", ..., "status":"closed"}

Błędy:
- `404` — HTTPException, format: `{"detail": "Zgłoszenie 'TCK-9999' nie istnieje."}`.
- `409` — HTTPException, format: `{"detail": "Nie można zmienić statusu z 'closed' na 'in_progress'."}`.
- `422` — standardowy format walidacji FastAPI/Pydantic: `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`.

Dobór kodów: `404`, gdy zasób nie istnieje; `409`, gdy dane są poprawne, ale operacja konfliktuje z aktualnym stanem (reguła biznesowa); `422`, gdy dane wejściowe są niepoprawne.

## 4. Testy i stan danych

**Stan z LAB1** (przed LAB2 testy używały `create_app()` bez parametrów; dziś fixture daje osobną bazę i atrapę Redis — sekcja 11):

| Scenariusz | Stan początkowy i żądanie | Oczekiwany status, treść i stan | Wynik rzeczywisty | Reset danych |
|---|---|---|---|---|
| Health | `GET /health` | 200, `{"status":"ok","service":"it-ticket-service"}` | PASSED | fixture `create_app()` |
| Lista i szczegóły | `GET /tickets`, potem `GET /tickets/{id}` znanego zgłoszenia | 200, pola zgodne z seedem | PASSED | fixture `create_app()` |
| Zapis → odczyt | `POST /tickets` z poprawnymi danymi, potem `GET /tickets/{id}` | 201 przy zapisie, 200 i te same dane przy odczycie | PASSED | fixture `create_app()` |
| Brak zasobu | `GET /tickets/UNKNOWN` | 404, `detail` zawiera `UNKNOWN` | PASSED | fixture `create_app()` |
| Błędne dane | `POST /tickets` z `title=""` | 422, liczba zgłoszeń przed/po bez zmian | PASSED | fixture `create_app()` |
| Konflikt / reguła biznesowa (niedozwolona) | `PATCH .../status` z `closed→in_progress` | 409, status po żądaniu wciąż `closed` | PASSED | fixture `create_app()` |
| Reguła biznesowa (dozwolona) | `PATCH .../status` z `new→in_progress` | 200, status = `in_progress` | PASSED | fixture `create_app()` |
| Restart | Uvicorn: `POST /tickets` → `GET` (widoczny) → `Ctrl+C` → start → `GET` | Nowy ticket zniknął, zostały 2 seedy | zgodny z oczekiwaniem | restart procesu |

Polecenie: `.\.venv\Scripts\python.exe -m pytest -q` → `9 passed` (z `tests/test_tickets.py`; łącznie z testem startowym po poprawie nazwy usługi: `10 passed`).

Eksperyment restartu: dane żyją w `TicketStore` tworzonym raz w `create_app()` — przetrwają między żądaniami HTTP w ramach jednego procesu, ale restart (też przez `--reload`) tworzy nową instancję aplikacji i nowy, pusty magazyn z ponownym seedem — stąd utrata zmian.

## 5. Porównanie architektur

Monolit:

    Klient → Jedna aplikacja (zgłoszenia + powiadomienia + użytkownicy) → Jedna baza

Modularny monolit:

    Klient → Jeden proces [moduł zgłoszeń | moduł powiadomień | moduł użytkowników] → Jedna baza

Mikrousługi:

    Klient → API Gateway → Ticket Service → Ticket DB
                          → Notification Service → Notification DB

1. Ma spójną, osobną odpowiedzialność biznesową i własne dane — w przeciwieństwie do klasy/modułu działa jako osobny proces i komunikuje się przez HTTP, nie wywołania funkcji.
2. Jako monolit: wszystkie funkcje (zgłoszenia, powiadomienia, użytkownicy) w jednym procesie i jednej bazie, wywołania bezpośrednio w kodzie.
3. Podział dodaje koszt sieci (latencja, serializacja) i ryzyko częściowej awarii (Notification Service może nie działać, Ticket Service musi to znosić).
4. Dla obecnej skali (jedna usługa, LAB1) wystarczyłby modularny monolit — mikrousługi mają sens, gdy Notification Service realnie trzeba skalować/wdrażać niezależnie.

## 6. Dokumentacja wykorzystana podczas pracy

| Odnośnik do fragmentu | Rozwiązywany problem | Co zastosowano w kodzie | Jak sprawdzono działanie |
|---|---|---|---|
| Request Body — https://fastapi.tiangolo.com/tutorial/body/ | Walidacja danych wejściowych `POST /tickets` | Model `TicketCreate` (Pydantic) z `Field(min_length, max_length)` | Test błędnych danych — pusty `title` → 422, brak zmiany stanu |
| Handling Errors — https://fastapi.tiangolo.com/tutorial/handling-errors/ | Zwracanie poprawnych kodów 404/409 z czytelnym komunikatem | `raise HTTPException(status_code=..., detail=...)` w endpointach | Testy 404 i 409 + manualny `GET /tickets/UNKNOWN` w `/docs` |

## 7. Ograniczenia i autorzy

Ograniczenia (LAB1; zmiany z LAB2 w sekcji 12): brak autoryzacji (kto może zmieniać status nie jest wymuszane w kodzie), brak paginacji/filtrowania.

Autorzy i wkład: (wpisz imię/nazwisko, numer albumu) — całość implementacji, testów i README.


---

# LAB2 — baza danych i cache

## 8. Zmiany względem LAB1 i konfiguracja

| Obszar | LAB1 | LAB2 |
|---|---|---|
| Magazyn | `TicketStore` (słownik w procesie) | SQLite przez SQLAlchemy 2.0 (sync), `app/repository.py` |
| Restart | dane znikają, seed przy każdym starcie | dane zostają; seed tylko do **pustej** tabeli |
| Odczyt `GET /tickets/{id}` | zawsze z pamięci | cache-aside w Redis, nagłówek `X-Cache: HIT\|MISS\|BYPASS` |
| `create_app()` | bez parametrów | `create_app(database_url=None, redis_client=None)`; zasoby w `lifespan` |
| Dodatkowo | — | `GET /ready` (rozszerzenie), pliki `database.py`, `db_models.py`, `cache.py` |
| Dla klienta | — | **bez zmian**: metody, ścieżki, ID `TCK-0001`, JSON, kody 404/409/422 (+ nagłówek `X-Cache`) |

Struktura: `app/main.py` (HTTP, fabryka), `models.py` (Pydantic + reguła przejść), `db_models.py` (ORM), `database.py` (silnik, sesje), `repository.py` (transakcje), `cache.py` (Redis).

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./app.db` | ścieżka bazy (względna do katalogu uruchomienia; `*.db*` jest w `.gitignore`) |
| `REDIS_URL` | `redis://localhost:6379/0` | adres Redis; klient: timeout 0,25 s, bez ponowień |
| `CACHE_PREFIX` | `lab2:it-ticket-service` | prefiks kluczy — ustaw własny, np. `lab2:12345:it-ticket-service` |
| `CACHE_TTL_SECONDS` | `60` | czas życia wpisu (musi być > 0) |

Schemat tworzy `Base.metadata.create_all` przy starcie (nie kasuje bazy i nie migruje istniejących kolumn; migracje Alembic to rozszerzenie). Sekretów nie zapisujemy w repozytorium.

## 9. Model danych, własność danych i reguła

Tabela `tickets`: `seq` (PK, AUTOINCREMENT), `title`, `description`, `reporter`, `priority`, `assignee`, `status`, `created_at`, `updated_at` (UTC). Publiczne ID `TCK-0001` = `seq` sformatowany na 4 cyfry; kanoniczny format sprawdza `parse_ticket_id` (np. `TCK-1`, `tck-0001` → 404, bez zapytania do bazy i cache). Numery nie są ponownie używane.

| Decyzja | Odpowiedź i uzasadnienie |
|---|---|
| Co musi przetrwać restart? | Zgłoszenia i ich statusy (to jedyne dane usługi). |
| Tabele i powiązania | Jedna tabela `tickets`, bez relacji (reporter/assignee to teksty — konta należą do innej domeny). `PRAGMA foreign_keys=ON` włączone na zapas. |
| Kto zapisuje? | Wyłącznie Ticket Service; Notification Service (plan) reaguje na zdarzenia i nie pisze do tych tabel — własność danych, nie osobny serwer. |
| Czego tu nie zapisujemy? | Historii powiadomień i kont użytkowników. |
| Baza vs reguła biznesowa | Baza: PK, `NOT NULL`, `CHECK` na `status`/`priority`, długości kolumn. Reguła przejść statusu: kod (`ALLOWED_TRANSITIONS` + `update_status`) — baza jej nie zna. |
| Co cache'ujemy? | `GET /tickets/{id}`; może być nieaktualny maks. TTL (60 s) tylko po nieudanym unieważnieniu. Lista nie jest cache'owana. |
| Klucz, TTL, unieważnianie | `{CACHE_PREFIX}:ticket:{id}`, TTL z `CACHE_TTL_SECONDS`, `DEL` po commicie w `PATCH`. |

Reguła z LAB1 (bez zmian): `new→in_progress`, `in_progress→resolved`, `resolved→in_progress`, `resolved→closed`, `closed→new`; ten sam status = dozwolony (aktualizuje `updated_at`, jak w LAB1). Przepływ `update_status`: odczyt wiersza → sprawdzenie reguły → `UPDATE ... WHERE seq=? AND status=<odczytany>` → commit. Wyjątek w bloku `sessions.begin()` = **rollback** (nic nie zapisano), a sesja jest zawsze zamykana. Warunkowy `UPDATE` wykrywa równoległą zmianę statusu (`ConcurrentUpdate` → 409), więc dwa żądania nie złamią reguły. Konflikt reguły i `404` nie unieważniają cache (nic się nie zmieniło).

## 10. Cache-aside — szczegóły

    GET: Redis GET → HIT: zwróć kopię
                   → MISS: baza → SET z TTL → zwróć (X-Cache: MISS)
                   → Redis niedostępny: baza, bez SET (X-Cache: BYPASS)
    PATCH: baza + COMMIT → DEL klucza (błąd DEL tylko logowany) → odpowiedź

- W cache trafia `Ticket.model_dump(mode="json")` (czysty JSON, nie obiekt ORM). Wpis uszkodzony lub niezgodny ze schematem = MISS i usunięcie.
- `404` nie jest cache'owany. `POST` nie wymaga unieważnień (nowy zasób nie ma jeszcze wpisu).
- Awaria Redis w `GET`/`SET`/`DEL` (`RedisError`) nie przerywa żądania; klient ma timeout 0,25 s i brak ponowień. Redis nie jest sprawdzany przy starcie — usługa wstaje bez niego.
- **Ograniczenie spójności:** commit w bazie i `DEL` w Redis to dwie operacje, nie jedna transakcja. Jeśli `DEL` się nie uda, stary wpis może być zwracany do końca TTL (test `test_failed_invalidation_leaves_stale_entry_until_ttl`). Prosty cache-aside nie daje też ścisłej spójności przy równoległym odczycie i zapisie (odczyt z bazy sprzed commitu może zapisać się w cache po `DEL`). Akceptowalne dla opisu zgłoszenia i statusu pokazywanego informacyjnie; **decyzji o dostępie (np. „czy można zamknąć zgłoszenie”) nie podejmujemy na podstawie kopii** — reguła zawsze czyta bazę.

## 11. Testy (36) i izolacja

`uv run pytest -q` → `36 passed`. Fixture (`tests/conftest.py`): osobna baza `tmp_path/test.db` + nowy `MemoryRedis` (`tests/fakes.py`: TTL na sztucznym zegarze `advance()`, przełącznik `down`), prefiks `lab2:test:it-ticket-service` przez `monkeypatch`. `with TestClient(app)` uruchamia `lifespan`.

| Plik | Wykazuje |
|---|---|
| `test_tickets.py`, `test_health.py` | scenariusze LAB1 (lista, szczegóły, zapis→odczyt, 404, 422, 409, reguła) na bazie |
| `test_persistence.py` | **trwałość** (zapis, nowa instancja z tą samą bazą i pustym cache), brak dublowania seeda, ciągłość ID, odrzucona operacja nie zmienia stanu (łącznie z `updated_at`), niepoprawne ID → 404, wszystkie dozwolone przejścia |
| `test_cache.py` | **HIT/MISS** (licznik wywołań repozytorium = 1), TTL ustawiony i wygasa, **unieważnienie**, konflikt nie kasuje wpisu, 404 nie jest cache'owany, **awaria Redis** (odczyt, zapis, create, list → 200, `BYPASS`), nieudane unieważnienie = stary wpis do TTL, uszkodzony wpis, `/ready` (ok/degraded/503) |
| `test_repository.py` | rollback po naruszeniu reguły, wyścig (`ConcurrentUpdate`), idempotentny seed |
| `test_cache_unit.py` | format klucza, walidacja TTL/prefiksu, GET/SET/DEL przy awarii |

Atrapa nie sprawdza prawdziwego Redis — stąd ręczna weryfikacja poniżej. Testy nie używają `FLUSHDB`/`FLUSHALL`.

## 12. Ręczna weryfikacja na prawdziwym Redis

Środowisko: Redis 7 lokalnie (`redis-server --port 6390 --save ""`), baza `sqlite:////tmp/m.db`, `CACHE_PREFIX=lab2:manual:it-ticket`, `CACHE_TTL_SECONDS=300`, Uvicorn na porcie 8101. Reset: usunięcie pliku bazy i `redis-cli DEL <klucz>` (nigdy `FLUSHALL`).

| # | Stan początkowy → żądanie | Oczekiwane | Rzeczywiste |
|---|---|---|---|
| 1 | pusta baza, pusty cache → `GET /tickets/TCK-0001` ×2 | `MISS`, potem `HIT` | `MISS`, `HIT` |
| 2 | `redis-cli TTL lab2:manual:it-ticket:ticket:TCK-0001` | ≤ 300 | `300` (tuż po zapisie) |
| 3 | `PATCH …/status {"status":"in_progress"}` → `GET` ×2 | 200; `MISS` z nowym statusem; `HIT` | 200; `MISS` (`in_progress`); `HIT` |
| 4 | `PATCH {"status":"new"}` z `in_progress`… | 409, stan i wpis bez zmian | 409; kolejny `GET` = `HIT`, `in_progress` |
| 5 | restart Uvicorna, `DEL` klucza → `GET` | zmiana z kroku 3 przetrwała, `MISS` | `MISS`, `in_progress` |
| 6 | `/ready` przy sprawnym Redis | 200 `ok` | `{"status":"ok","database":"ok","cache":"ok"}` |
| 7 | zatrzymanie Redis → `GET`, `PATCH {"status":"resolved"}`, `/ready` | `BYPASS` + dane z bazy; zapis 200; 200 `degraded` | `BYPASS`, `in_progress`; zapis → `resolved`; `degraded`; w logu: `cache BYPASS (GET …)`, `DEL … nieudany (stary wpis do końca TTL)` |

Odtworzenie starego wpisu przy działającym Redis (klient odłączony): ustaw `CACHE_TTL_SECONDS=300`, `GET` (MISS, wpis w cache), zatrzymaj Uvicorna, uruchom go z `REDIS_URL=redis://127.0.0.1:9999/0` (nieużywany port), wykonaj `PATCH` (zapis OK, `DEL` nieudane), wróć do poprawnego `REDIS_URL` i zrestartuj — `GET` zwróci `HIT` ze starym statusem do końca TTL; po wygaśnięciu (`redis-cli TTL`) `MISS` z aktualnym. Ten sam mechanizm automatycznie dowodzi test `test_failed_invalidation_leaves_stale_entry_until_ttl`.

### Uruchomienie Redis (wybrany wariant)

| Operacja | Docker | WSL2 | Memurai |
|---|---|---|---|
| start | `docker run --name lab2-redis -p 127.0.0.1:6379:6379 -d redis:7-alpine` | `sudo service redis-server start` | usługa Windows |
| ping / TTL / DEL | `docker exec lab2-redis redis-cli ping \| TTL <klucz> \| DEL <klucz>` | `redis-cli …` | `memurai-cli …` |
| awaria / powrót | `docker stop lab2-redis` / `docker start lab2-redis` | `sudo service redis-server stop` / `start` | `Stop-Service` / `Start-Service Memurai` |

## 13. Porównanie, ograniczenia, źródła i autorzy

**Porównanie**

| | Pamięć procesu (LAB1) | SQLite | Redis (cache) |
|---|---|---|---|
| Rola | stan procesu | trwałe źródło prawdy | odtwarzalna kopia |
| Restart Uvicorna | zmiany znikają | plik zostaje | osobny proces, wpis może zostać |
| Brak danych | stan utracony | brak rekordu | MISS jest normalny |
| Trudność | utrata zmian | schemat, transakcje, błędy | aktualność, TTL, awarie |

- *Co się zmieniło dla klienta, a co w środku?* Klient: nic poza nagłówkiem `X-Cache` i trwałością danych. W środku: repozytorium, transakcje, cache.
- *Czy baza i cache czynią z usługi mikroserwis?* Nie — to dalej jeden proces. O mikroserwisowości decydują granica odpowiedzialności i własność danych; druga usługa (Notification Service) nie może pisać do tabeli `tickets`, dostaje dane przez HTTP/zdarzenia.
- *Awarie:* proces aplikacji — dane są w bazie, po restarcie usługa działa dalej; Redis — usługa działa wolniej/bez cache (`BYPASS`, `/ready` = `degraded`), ryzyko starego wpisu do TTL; baza — brak odczytu/zapisu (`/ready` = 503), cache nie zastępuje bazy.
- *Dopuszczalne opóźnienie danych:* do 60 s i tylko po nieudanym unieważnieniu; status zgłoszenia jest informacją dla człowieka, nie warunkiem decyzji.
- *SQLite → PostgreSQL:* sterownik (`psycopg`) i `DATABASE_URL`, pętla `PRAGMA` tylko dla SQLite, `AUTOINCREMENT` → sekwencja/`IDENTITY`, typ `DateTime(timezone=True)` zamiast `UTCDateTime`, migracje (Alembic) zamiast `create_all`, pula połączeń, poziom izolacji i testy na docelowej bazie (ORM nie gwarantuje pełnej zgodności).

**Ograniczenia:** brak autoryzacji, paginacji, cache listy, migracji; `create_all` nie zmienia istniejących kolumn; cache-aside bez ścisłej spójności (sekcja 10); atrapa Redis nie odwzorowuje pełnego protokołu; seed przy wielu równoległych procesach startujących na pustej bazie może się zdublować (jedna instancja — brak problemu).

**Zastosowana dokumentacja**

| Fragment | Problem | Zastosowanie | Sprawdzenie |
|---|---|---|---|
| SQLAlchemy 2.0: [Session Basics — framing out a begin/commit/rollback block](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block) | commit/rollback i zamykanie sesji na operację | `sessionmaker.begin()` w `repository.py`; wyjątek = rollback | `test_rule_violation_raises_and_rolls_back`, `test_rejected_transition_leaves_state_unchanged_after_restart` |
| redis-py: [Connecting to Redis / timeouts](https://redis.readthedocs.io/en/stable/connections.html) | niedostępny Redis nie może wieszać żądań | `socket_connect_timeout`, `socket_timeout`, `retry_on_timeout=False`, `decode_responses=True` w `make_redis`; łapanie `RedisError` | test awarii + ręczny krok 7 (`BYPASS`) |
| FastAPI: [Lifespan Events](https://fastapi.tiangolo.com/advanced/events/) i [Testing](https://fastapi.tiangolo.com/tutorial/testing/) | tworzenie/sprzątanie zasobów; test z własną bazą | `lifespan` w `create_app`; `with TestClient(app)` w fixture | `test_data_survives_restart_with_empty_cache` |
| SQLAlchemy 2.0: [SQLite — Foreign Key Support](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support) | `PRAGMA foreign_keys` domyślnie wyłączone | event `connect` w `database.py` | przegląd kodu (usługa nie ma jeszcze relacji) |


**Autorzy i wkład:** (wpisz imię/nazwisko, numer albumu) — całość etapu LAB2.

**Plan drugiej usługi:** bez zmian (Notification Service); w LAB3 rejestracja i odnajdywanie przez Eurekę.
