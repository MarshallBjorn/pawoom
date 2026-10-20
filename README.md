# IT Ticket Service — LAB1

Mikrousługa do rejestrowania i obsługi zgłoszeń IT (helpdesk).

## Uruchomienie i testy

Wymagania: Python 3.12+, PowerShell. Terminal w katalogu z tym README i `requirements.txt`.

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m pytest -q
    .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8101

W drugim terminalu: `Invoke-RestMethod http://localhost:8101/health`. Dokumentacja: Swagger UI na `http://localhost:8101/docs`. Zatrzymanie: `Ctrl+C`.

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
| GET | `/tickets` | Lista zgłoszeń | 200 | — |
| GET | `/tickets/{id}` | Szczegóły zgłoszenia | 200 | 404 |
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

Testy własne w `tests/test_tickets.py` (9 testów), każdy z osobną instancją `create_app()`:

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

## 7. Ograniczenia, autorzy i AI

Ograniczenia: dane tylko w pamięci procesu (brak bazy/trwałości), brak autoryzacji (kto może zmieniać status nie jest wymuszane w kodzie), brak paginacji/filtrowania, każda instancja aplikacji ma własny, niezależny magazyn.

Autorzy i wkład: (wpisz imię/nazwisko) — całość implementacji, testów i README.

AI: Claude wykorzystany jako pomocnik do napisania testów (`tests/test_tickets.py`) i uzupełnienia README; kod uruchomiono i zweryfikowano samodzielnie (`pytest -q`, manualne żądania przez `/docs` i `Invoke-RestMethod`).