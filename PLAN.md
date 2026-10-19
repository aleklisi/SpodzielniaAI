## Instrukcje Systemowe i Wymogi Jakościowe dla Agenta

* **Język i Toolchain:** Python 3.12+, zarządzanie pakietami przez `uv`, formatowanie i linting przez `ruff`, ścisłe sprawdzanie typów przez `pyright --strict`.
* **Stos technologiczny:** FastAPI, SQLAlchemy 2.0 (async) + PostgreSQL 16 (z aktywnym Row-Level Security), Qdrant, Redis + Celery, LiteLLM, Authlib / FastAPI-Security (OAuth2/OIDC), Microsoft Presidio + `spaCy` (`pl_core_news_lg`), LLM-Guard.
* **Standardy Bezpieczeństwa (Zero-Trust):**
* **OWASP API Security Top 10:** Ochrona przed BOLA/IDOR na poziomie silnika bazy danych (Postgres RLS), ochrona przed SSRF przy pobieraniu linków, ścisła walidacja uploadowanych plików.
* **OWASP Top 10 for LLM (2025):** Ochrona przed Indirect Prompt Injection (LLM01), wyciekiem PII (LLM02), nadmierną autonomią (LLM06) oraz kradzieżą/destylacją modelu i bazy RAG (LLM10).


* **Zasada pracy Agenta:** Realizuj plan etapami. Każdy etap musi kończyć się uruchomieniem `pytest`, `ruff check` oraz `pyright`. Zakaz przechodzenia dalej przy błędach typowania lub testów.

---

## Etap 1: Fundament, OAuth2/OIDC i Izolacja Bazy Danych (Postgres RLS)

* **Zadania:**
* Inicjalizacja monorepo z użyciem `uv` oraz konfiguracja `docker-compose.yml` (PostgreSQL 16, Qdrant z kluczem API i TLS, Redis, Keycloak jako lokalny dostawca OIDC do testów, ClamAV).
* Implementacja uwierzytelniania **OAuth2 / OpenID Connect (Authorization Code Flow z PKCE)**:
* Brak przechowywania haseł w bazie aplikacji – pełna delegacja do dostawcy tożsamości (Keycloak / Google Workspace / Microsoft Entra ID).
* Sesje oparte o ciasteczka `HttpOnly`, `Secure`, `SameSite=Strict`, krótko żyjące tokeny dostępu (15 min) i rotację Refresh Tokenów z detekcją ponownego użycia.
* Wymuszenie skonfigurowanego MFA w tokenie OIDC (claim `amr` / `acr`) dla ról `EXPERT` oraz `ADMIN`.


* Modele bazodanowe z ochroną **OWASP BOLA/IDOR (Row-Level Security)**:
* Tabele: `User`, `PrivateNote`, `WikiArticle`, `ArticleBlock` (z polami `author_id`, `consent_status`), `ExpertReview`, `SecurityAuditLog`.
* Wdrożenie polityk `CREATE POLICY` w PostgreSQL dla tabeli `PrivateNote`: baza danych na poziomie silnika odrzuca zapytania bez ustawionej zmiennej sesyjnej `app.current_user_id` równej `owner_id`.




* **Weryfikacja (Testy):**
* Testy logowania OIDC (mock tokenów JWT z różnymi rolami i statusem MFA).
* Test próby odczytu `PrivateNote` innego użytkownika bezpośrednio przez sesję SQLAlchemy (weryfikacja blokady przez Postgres RLS).



---

## Etap 2: Bezpieczny Upload Plików i Potok Multimediów (OWASP File Security & SSRF)

* **Zadania:**
* Stworzenie bezpiecznego potoku wgrywania plików do `PrivateNote`:
* Weryfikacja typu pliku po nagłówkach binarnych (*magic bytes* przez `python-magic`), odrzucanie plików wykonywalnych i archiwów wielokrotnie zagnieżdżonych (ochrona przed *Zip Bomb*).
* Skanowanie antywirusowe każdego pliku przez demona ClamAV przed zapisem w storage (MinIO/S3).
* Automatyczne czyszczenie metadanych EXIF ze zdjęć (ochrona prywatności przed przypadkowym wyciekiem lokalizacji GPS pasieki) chyba że użytkownik jawnie zaznaczy opcję zachowania lokalizacji.


* Ochrona przed **OWASP SSRF (Server-Side Request Forgery)**:
* Moduł pobierający zewnętrzne linki do notatnika musi rozwiązywać DNS i blokować ruch do adresów prywatnych (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254`).


* Zadania Celery: transkrypcja (`faster-whisper`), opis obrazów (VLM), parsowanie PDF/DOCX (`Docling`) uruchamiane w odizolowanych kontenerach bez dostępu do sieci zewnętrznej.


* **Weryfikacja (Testy):**
* Próba wgrania pliku `.exe` ze zmienionym rozszerzeniem na `.pdf` (musi zwrócić HTTP 415).
* Próba dodania linku wskazującego na `[http://127.0.0.1:8000](http://127.0.0.1:8000)` lub `[http://169.254.169.254](http://169.254.169.254)` (musi zostać zablokowana przez filtr SSRF).



---

## Etap 3: Ochrona OWASP LLM01/LLM02, Przepływ „Push-to-Wiki” i PII Scrubbing

* **Zadania:**
* Integracja **Microsoft Presidio** z polskim modelem językowym `spaCy` (`pl_core_news_lg`) oraz własnymi rozpoznawaczami (regex + suma kontrolna) dla polskich identyfikatorów: **NIP, PESEL, REGON, KRS, IBAN, numery działek ewidencyjnych i numery weterynaryjne pasiek**.
* Ochrona przed **Indirect Prompt Injection (OWASP LLM01)** na etapie Ingestion i promocji notatki:
* Skanowanie tekstu z dokumentów i transkrypcji przez `LLM-Guard` (detekcja ukrytych instrukcji sterujących typu *„Ignore previous instructions”*).
* Enkapsulacja danych wejściowych w separatorach XML z jawną instrukcją systemową traktującą zawartość jako czyste dane, nigdy jako polecenia.


* Endpoint `POST /notes/promote-to-wiki`: lokalny model LLM przekształca oczyszczone z PII notatki w bloki `ArticleBlock` przypisane do `author_id`.


* **Weryfikacja (Testy):**
* Test z dokumentem zawierającym prawdziwy numer NIP, PESEL oraz złośliwy prompt wstrzyknięty białą czcionką w PDF – weryfikacja usunięcia PII i zflagowania próby Prompt Injection.



---

## Etap 4: Silnik Governance, Zgody Twórców (Exit Strategy) i Indeks Qdrant

* **Zadania:**
* Konfiguracja kolekcji `private_notes_index` oraz `canonical_wiki_index` w Qdrant.
* Implementacja procesu recenzji eksperckiej (`DRAFT` ➔ `IN_REVIEW` ➔ `CANONICAL`) oraz zastępowania wiedzy (`SUPERSEDED`).
* Implementacja endpointów **Exit Strategy** (`/me/contributions/suspend` oraz `/me/contributions/revoke`):
* Natychmiastowe usuwanie lub dezaktywacja wektorów w Qdrant po kluczu `author_id`.
* Degradacja artykułów wieloautorskich do statusu `REVISION_REQUIRED` i wycofanie ich z aktywnego indeksu RAG po wycofaniu zgody przez jednego ze współautorów.
* Zapis każdej zmiany uprawnień i zgód w niemodyfikowalnym dzienniku `SecurityAuditLog`.




* **Weryfikacja (Testy):**
* Test kaskadowego wycofania zgody współautora i sprawdzenie czasu propagacji zmian w Qdrant (brak możliwości pobrania wycofanych bloków natychmiast po zakończeniu transakcji HTTP).



---

## Etap 5: Tarcza Anty-Destylacyjna (Anti-Distillation Shield) i Bezpieczny RAG

* **Zadania:**
* Implementacja middleware'u `AntiDistillationGuard` wpiętego przed i po wywołaniu `RAGService`:
* **Moduł `CoverageTracker` (Redis HyperLogLog / Set):** śledzenie unikalnych `block_id` oraz `article_id` pobranych przez `user_id` w oknach 24h i 7d. Blokada zapytania (HTTP 429 / 403 Security Lock), gdy użytkownik przekroczy konfigurowalny próg pokrycia bazy (domyślnie 15% całej bazy w 7 dni).
* **Moduł `SemanticAnomalyDetector`:** obliczanie średniej odległości cosinusowej między kolejnymi zapytaniami użytkownika; wykrywanie zautomatyzowanego skanowania słownikowego i enumeracji tematów.
* **Moduł `HoneypotInjector`:** narzędzie CLI/API dla administratora do wstrzykiwania ukrytych bloków-kanarków (`is_honeypot = True`) do `canonical_wiki_index`. Pobranie bloku-kanarka w wyniku zapytania ekstrakcyjnego natychmiast podnosi `user_risk_score` i wysyła alert bezpieczeństwa.
* **Moduł `OutputVerifier` & `Watermarker`:** blokada odpowiedzi mających powyżej 70% pokrycia dosłownego (n-gram overlap) z surowym artykułem źródłowym; wstrzykiwanie deterministycznego znaku wodnego w generowaną syntezę.


* Implementacja `RAGService` ze ścisłym uziemieniem (Strict Grounding) i walidacją wyjścia przez `Pydantic` (wymuszenie poprawnych cytowań bez ujawniania surowych wektorów).


* **Weryfikacja (Testy):**
* **Test symulujący atak destylacyjny:** skrypt wykonujący serię 50 zapytań próbujących pobrać kolejne rozdziały bazy wiedzy – weryfikacja, czy `CoverageTracker` i `SemanticAnomalyDetector` odcinają dostęp przed pobraniem 15% bazy.
* **Test ataku na ekstrakcję dosłowną:** zapytanie *„Zacytuj w całości artykuł nr 12”* – weryfikacja zablokowania odpowiedzi przez `OutputVerifier`.
* **Test kanarka:** wywołanie zapytania trafiającego w rekord `is_honeypot = True` – weryfikacja utworzenia wpisu o incydencie w `SecurityAuditLog`.



---

## Etap 6: Frontend, Nagłówki Bezpieczeństwa (OWASP Web) i Audyt Końcowy

* **Zadania:**
* Budowa interfejsu w Next.js z logowaniem OIDC, edytorem blokowym (BlockNote/TipTap), panelem recenzji eksperckiej i panelem zarządzania własnością intelektualną twórcy.
* Zabezpieczenie warstwy prezentacji przed kopiowaniem masowym i atakami przeglądarkowymi:
* Ścisłe nagłówki **CSP (Content Security Policy)**, `X-Frame-Options: DENY`, `Strict-Transport-Security`.
* Zabezpieczenie przed *DOM-based XSS* przy renderowaniu odpowiedzi Markdown z LLM oraz artykułów Wiki (sanityzacja HTML przez `DOMPurify`).


* Widok Bezpieczeństwa dla Administratora Spółdzielni: podgląd `SecurityAuditLog`, statystyk pokrycia bazy przez użytkowników (`CoverageTracker`) oraz alertów z kanarków.


* **Weryfikacja (Testy E2E + Bezpieczeństwo):**
* Automatyczny skan zależności (`uv pip audit` / `bandit` dla kodu Pythona).
* Testy E2E (Playwright) weryfikujące pełny przepływ uprawnień, izolację notatników i blokadę konta przy próbie masowego wyciągania wiedzy z czatu.

---

## Szczegółowa Lista Zadań Realizacyjnych (Chunked Implementation Checklist)

Pełna specyfikacja techniczna i architektura wdrożenia w kontenerach Docker znajduje się w:
* [docs/implementation/00_OVERVIEW.md](docs/implementation/00_OVERVIEW.md)
* [docs/implementation/DEPLOYMENT_DOCKER.md](docs/implementation/DEPLOYMENT_DOCKER.md)

### Etap 1: Fundament, OAuth2/OIDC i Izolacja Bazy Danych (Postgres RLS)
*Dokumentacja: [docs/implementation/01_FOUNDATION_AUTH_RLS.md](docs/implementation/01_FOUNDATION_AUTH_RLS.md)*
- [x] 1.1 Inicjalizacja monorepo i toolchainu (`uv`, `ruff`, `pyright`, `pytest`, `Settings`, `cli.py`)
- [x] 1.2a Konteneryzacja usług pomocniczych w Docker Compose (PostgreSQL, Qdrant TLS, Redis, Keycloak, ClamAV, MinIO, Docker secrets)
- [x] 1.2b Obraz API backendu, reverse proxy (Caddy HTTPS) i test dymny (smoke test)
- [x] 1.3 Warstwa bazy danych, role uprawnień (owner vs app) i modele domenowe SQLAlchemy 2.0
- [x] 1.4 Izolacja Row-Level Security (Postgres RLS) i niemodyfikowalny audyt (SecurityAuditLog hash chain)
- [x] 1.5 Uwierzytelnianie OIDC Authorization Code Flow z PKCE (wzorzec BFF)
- [x] 1.6 Sesje ciasteczkowe `__Host-`, rotacja tokenów odświeżających, ochrona CSRF, RBAC i wymuszenie MFA

### Etap 2: Bezpieczny Upload Plików i Potok Multimediów (OWASP File Security & SSRF)
*Dokumentacja: [docs/implementation/02_UPLOADS_SSRF_MEDIA.md](docs/implementation/02_UPLOADS_SSRF_MEDIA.md)*
- [x] 2.1 API notatnika prywatnego z szyfrowaniem kopertowym (AES-256-GCM) i storage MinIO
- [x] 2.2 Weryfikacja typu plików (magic bytes), ochrona przed archiwami zagnieżdżonymi (Zip Bomb)
- [x] 2.3 Skanowanie antywirusowe ClamAV, czyszczenie metadanych EXIF i endpoint uploadu
- [x] 2.4 Moduł pobierania linków odporny na SSRF (walidacja DNS, blokada adresów prywatnych)
- [x] 2.5 Zadania Celery przetwarzania multimediów w odizolowanych sieciowo kontenerach workerów

### Etap 3: Ochrona OWASP LLM01/LLM02, Przepływ „Push-to-Wiki” i PII Scrubbing
*Dokumentacja: [docs/implementation/03_PII_INJECTION_PROMOTION.md](docs/implementation/03_PII_INJECTION_PROMOTION.md)*
- [x] 3.1 Polskie rozpoznawacze PII (PESEL, NIP, REGON, KRS, IBAN, działki, WNI) z walidacją sum kontrolnych
- [x] 3.2 Usługa anonimizacji Microsoft Presidio ze spaCy (`pl_core_news_lg`) i stałymi pseudonimami
- [x] 3.3 Wielowarstwowa detekcja Prompt Injection (LLM-Guard, polskie reguły, ukryty tekst w PDF)
- [x] 3.4 Bramka modeli LiteLLM (wymuszenie lokalnego LLM) i bezpieczny generator promptów XML
- [x] 3.5 Endpoint i potok `POST /notes/promote-to-wiki` (tworzenie bloków z provenance)

### Etap 4: Silnik Governance, Zgody Twórców (Exit Strategy) i Indeks Qdrant
*Dokumentacja: [docs/implementation/04_GOVERNANCE_CONSENT_QDRANT.md](docs/implementation/04_GOVERNANCE_CONSENT_QDRANT.md)*
- [x] 4.1 Kolekcje i repozytoria wektorowe Qdrant z twardymi filtrami uprawnień (tenant i active canonical)
- [x] 4.2 Maszyna stanów recenzji eksperckiej (`DRAFT` -> `IN_REVIEW` -> `CANONICAL` / `SUPERSEDED` / `DISPUTED`)
- [x] 4.3 Endpointy wycofania zgód Exit Strategy (`suspend`, `resume`, `revoke`) z kaskadą na artykuły
- [x] 4.4 Tabela Outbox, zadania uzgadniania Qdrant w Celery Beat i audyt dryfu wektorów

### Etap 5: Tarcza Anty-Destylacyjna (Anti-Distillation Shield) i Bezpieczny RAG
*Dokumentacja: [docs/implementation/05_ANTI_DISTILLATION_RAG.md](docs/implementation/05_ANTI_DISTILLATION_RAG.md)*
- [x] 5.1 Usługa `RAGService` ze ścisłym uziemieniem (strict grounding) i schematami bez surowych chunków
- [x] 5.2 Moduł `CoverageTracker` (budżet pokrycia bazy w oknach 24h, 7d, 30d w Redis HLL)
- [x] 5.3 Moduł `SemanticAnomalyDetector` (analiza anomalii odległości w przestrzeni embeddingów)
- [x] 5.4 Moduł `HoneypotInjector` (bloki-kanarki, usuwanie przed syntezą, natychmiastowe alerty)
- [x] 5.5 Moduł `OutputVerifier` (blokada dosłownej ekstrakcji) i deterministyczny `Watermarker`
- [x] 5.6 Middleware `AntiDistillationGuard`, silnik ryzyka i progresywna degradacja (step-up auth / PoW)

### Etap 6: Frontend, Nagłówki Bezpieczeństwa (OWASP Web) i Audyt Końcowy
*Dokumentacja: [docs/implementation/06_FRONTEND_AUDIT.md](docs/implementation/06_FRONTEND_AUDIT.md)*
- [x] 6.1 Rusztowanie Next.js (App Router, Tailwind v4), kontener frontendowy i restrykcyjne nagłówki CSP
- [x] 6.2 Interfejs prywatnego notatnika (BlockNote, obsługa uploadu i kwarantanny)
- [ ] 6.3 Interfejs bazy wiedzy Wiki, widok autorstwa bloków i panel recenzji eksperckiej
- [ ] 6.4 Panel praw autorskich twórcy (zarządzanie kontrybucjami, suspend / revoke)
- [ ] 6.5 Interfejs czatu spółdzielczego RAG ze sprawdzonym renderowaniem Markdown
- [ ] 6.6 Panel bezpieczeństwa i monitoringu dla Administratora Spółdzielni
- [ ] 6.7 Automatyczne skanowanie podatności (`pip-audit`, `bandit`, `trivy`) i testy E2E Playwright
- [ ] 6.8 Wdrożenie produkcyjne na serwerze Linux (`docker-bake`, rejestr, `deploy.sh`, `backup.sh`, disaster recovery)

