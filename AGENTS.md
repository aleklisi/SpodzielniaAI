# Reguły Projektu: Spółdzielnia AI

Ten plik czytają ludzie i agenci AI, którzy kontynuują rozwój projektu. Sekcje 3 i 4 są
obowiązkowe; pozostałe zbierają wnioski z dotychczasowych etapów (1–14), żeby nie odkrywać ich
od nowa.

## 1. Stos Technologiczny i Narzędzia
- **Język:** Python 3.12+ (zarządzany wyłącznie przez `uv`).
- **Backend:** FastAPI, SQLAlchemy 2.0 (async) + Alembic, Pydantic v2.
- **Bazy i Kolejki:** PostgreSQL 16 (z Row-Level Security), Qdrant, Redis + Celery.
- **AI & NLP:** LiteLLM (każdy model przez API zgodne z OpenAI, konfigurowany w `/admin/llm`), Docling, Presidio + spaCy (`pl_core_news_lg`), klasyfikator prompt injection.
- **Frontend:** Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS v4, BlockNote, TanStack Query, `openapi-fetch`, `next-intl` (polski i angielski).
- **Infrastruktura:** Docker Compose (`compose.yaml` + `compose.dev.yaml` / `compose.prod.yaml`), Caddy (TLS, CSP), Keycloak (OIDC, TOTP), MinIO, ClamAV.

## 2. Komendy Weryfikacyjne (Uruchamiaj po każdej zmianie)
Backend (z katalogu głównego):
- Dodawanie pakietów: `uv add <pakiet>`
- Linting i formatowanie: `uv run ruff check --fix . && uv run ruff format .`
- Kontrola typów (obowiązkowa, tryb strict): `uv run pyright`
- Testy: `uv run pytest` (pełny zestaw, ~7 min; testy integracyjne startują własne kontenery przez testcontainers). Szybko: `make test` (jednostkowe), `uv run pytest -m integration tests/integration/test_x.py`.

Frontend (z `frontend/`):
- `npm run lint && npm run typecheck && npx vitest run && npm run build`
- Po zmianie API: `npm run gen:api` (regeneruje `openapi.json` i `src/lib/api-schema.d.ts`; test `test_committed_openapi_schema_is_current` pilnuje zgodności).

E2E (Playwright, na działającym stosie deweloperskim):
- `make up` (buduje i startuje wszystko), `make migrate` (migracje), po zmianach frontendu: `docker compose -f compose.yaml -f compose.dev.yaml up -d --build --wait frontend`.
- `make e2e` (cd frontend && npx playwright test). Testy idą szeregowo (wspólny stos, zmieniają stan kont); projekty `chromium`, `tablet`, `mobile`.

## 3. Żelazne Zasady Architektoniczne (Nienaruszalne)
1. **Izolacja (RLS):** Każde zapytanie do `PrivateNote` musi przechodzić przez sesję z ustawionym `app.current_user_id`. Nigdy nie polegaj wyłącznie na filtrach `WHERE` w kodzie Pythona.
2. **Block-Level Provenance:** Każdy blok tekstu (`ArticleBlock`) i odpowiadający mu wektor w Qdrant musi posiadać `author_id` oraz `consent_status` (`ACTIVE`, `SUSPENDED`, `REVOKED`).
3. **Anti-Distillation:** API nigdy nie zwraca surowych chunków z bazy Qdrant do klienta. Zwracana jest wyłącznie synteza z modelu LLM po przejściu przez `AntiDistillationGuard`. Każda nowa droga wyjścia wiedzy (nowy format, język, eksport) musi przejść te same kontrole — np. odpowiedzi po angielsku przechodzą dodatkowo kontrolę tłumaczenia (`shield/translation.py`); gdy kontrola nie może się wykonać, system odmawia albo wraca do sprawdzonej ścieżki (fail closed).
4. **Dane wrażliwe tylko u dopuszczonych dostawców:** Anonimizacja (PII Scrubbing), prywatne notatki, szkice promocji, obrazy i embeddingi danych prywatnych trafiają wyłącznie do dostawców modeli dopuszczonych do danych prywatnych (lista operatora `LLM_PRIVATE_DATA_ENDPOINTS` w `.env` + zgoda ADMIN z MFA). Wymuszaj flagę `private_data=True`; brak dopuszczonego dostawcy wyłącza funkcję — nigdy nie przełączaj na innego dostawcę. (Zmiana z „tylko lokalny LLM”, Etap 10, US-04.)

## 4. Cykl Pracy (Workflow)
- Przed rozpoczęciem pracy przeczytaj `PLAN.md` i zidentyfikuj pierwszy niezrealizowany podpunkt `- [ ]`.
- Doczytaj szczegóły biznesowe z `docs/ARCHITECTURE.md` oraz wymogi bezpieczeństwa z `docs/SECURITY.md`; przy etapach rozszerzeń także `docs/implementation/NN_*.md` i `docs/USER_STORIES_LOG.md`.
- Napisz kod oraz testy jednostkowe/integracyjne (a dla zmian w interfejsie: Vitest i E2E).
- Uruchom `ruff`, `pyright` oraz `pytest` (i weryfikację frontendu, jeśli go zmieniałeś).
- Dopiero gdy wszystkie testy przechodzą na zielono, oznacz zadanie jako `- [x]` w `PLAN.md` i wykonaj git commit.
- NIE przechodź do kolejnego etapu bez wyraźnego polecenia użytkownika.
- **Repozytorium jest lokalne:** nie dodawaj zdalnego repozytorium i nie wysyłaj (`git push`) kodu bez wyraźnej zgody właściciela.

### 4.1. Nowa historia użytkownika (etap rozszerzenia)
Prośby spoza pierwotnego planu (Etapy 1–6) przechodzą dwa kroki, każdy z osobnym commitem:
1. **Doprecyzowanie i plan** (`docs(stage-N): plan …`): zadaj właścicielowi 2–4 pytania o decyzje, które naprawdę są jego (zakres, miejsce przechowywania, domyślne zachowanie), z rekomendowaną opcją. Następnie:
   - `docs/implementation/NN_NAZWA.md`: *Refined requirement* (tabela decyzji), *Acceptance criteria* (Gherkin), *Design* (reguły R1…), *Chunks* (N.1…N.k z testami), *Risks and notes*;
   - `PLAN.md`: sekcja „Etap N” z podpunktami `- [ ]` (po polsku);
   - `docs/USER_STORIES_LOG.md`: wiersz w tabeli i sekcja `US-NN` (status PLANNED).
2. **Implementacja** (`feat(stage-N): …`) na wyraźne polecenie. Na końcu dopisz w pliku etapu sekcje *Implementation* (gdzie co jest), *Changes against the plan* (każde odstępstwo z powodem) i *Tests* (wyniki liczbowe, znane porażki), dodaj sekcję etapu w `docs/SECURITY_REVIEW.md` (kontrola → dowód w testach, ryzyka zaakceptowane), oznacz `- [x]` i US jako DONE z hashami commitów.
- Commity kończą się linią `Co-Authored-By:` zgodną z instrukcją narzędzia, z którego korzystasz.
- Raportuj uczciwie: jeśli test nie przechodzi z przyczyn środowiskowych, podaj przyczynę i dowód, nie ukrywaj go.

## 5. Konwencje Backendu
- **Migracje Alembic:** identyfikatory `NNNN_nazwa` (kolejne numery, liniowa historia). Konwencja nazw w metadanych dodaje prefiksy (`ck_<tabela>_…`, `fk_…`, `pk_…`): w `create_table` podawaj nazwy CHECK bez prefiksu, w `op.create_check_constraint`/`op.drop_constraint` używaj `op.f("ck_tabela_nazwa")`. Każdą nową tabelę i migrację dopisz w `tests/unit/test_db_models.py` (`test_tables_registered`, `test_migrations_form_a_single_linear_history`).
- **Ograniczenia w bazie, nie tylko w kodzie:** wartości trafiające do CSS, HTML lub cookie (kolory, kody języka) mają CHECK w bazie, walidację Pydantic i ponowną walidację w czytniku. Kolumny JSONB, które mogą być puste, deklaruj `JSONB(none_as_null=True)`.
- **Endpointy administracyjne:** `elevated_router(Role.ADMIN)` (rola + MFA), CSRF jest globalny dla metod niebezpiecznych, limit w Redisie (okno godzinowe; fail open tylko dla ustawień niewrażliwych), zapis w `AuditService(session).record(...)` w tej samej transakcji, bez treści (np. bez bajtów obrazów, bez treści pytań).
- **Błędy API to kody maszynowe:** `detail={"error": "kod", ...}`; teksty dla ludzi są we frontendzie. Teksty, które serwer musi napisać sam (np. odmowa czatu), są w `spoldzielnia_ai/i18n.py` w obu językach i wybierane przez `locale_or_default(principal.user.locale)`. W bazie przechowuj kody, nie zdania (np. `review_alert = "revision_required"`).
- **Pliki od użytkowników:** walidacja po zawartości (libmagic), limity rozmiaru i pikseli przed dekodowaniem, ClamAV (brak skanera = odmowa), ponowne kodowanie bez metadanych; SVG zawsze odrzucany.
- **Modele LLM:** każda funkcja ma swój „purpose” i model wybierany w `/admin/llm`; brak modelu → `503 model_not_configured` i funkcja wyłączona w UI (nie awaria). Zmiana promptu = nowa `version` szablonu w `llm/prompts.py`.
- **Ruff:** unikaj w docstringach i stringach znaków niejednoznacznych (×, –, „ ”) albo oznacz linię `# noqa: RUF001`; linie do 100 znaków.
- **Obraz Dockera** kopiuje tylko wskazane pliki (`docker/backend.Dockerfile`): plik dopisany do `pyproject.toml` (np. `license-files`) musi być też skopiowany do obrazu.

## 6. Konwencje Frontendu
- **Next.js 16 różni się od wersji znanych z dokumentacji ogólnej:** przed zmianą API frameworka przeczytaj `frontend/node_modules/next/dist/docs/` (zob. `frontend/AGENTS.md`).
- **CSP z nonce i Trusted Types:** żadnych atrybutów `style="…"` w HTML i żadnych skryptów inline. Dynamiczne style ustawiaj przez CSSOM (`element.style.setProperty`) albo jeden `<style nonce>` w layoucie. Markdown renderuj tylko przez `SafeMarkdown`. Każda strona renderuje się per żądanie (nonce).
- **Układ:** powłoka w `components/shell/` (menu po lewej, obszar roboczy po prawej, szuflada na telefonie). Strony układaj zapytaniami kontenerowymi (`@3xl:` …), nie szerokością okna; cele dotykowe ≥ 44 px; brak przewijania w poziomie przy 360 px; łącz klasy przez `cn` (tailwind-merge).
- **Teksty interfejsu (Etap 14):** nigdy nie wpisuj tekstu w kod.
  - Każdy tekst trafia do `src/messages/pl/<obszar>.json` **i** `src/messages/en/<obszar>.json` (ICU; liczba mnoga po polsku: `one/few/many/other`). Nowy obszar dopisz w `src/messages/index.ts`. Słownik terminów: `src/messages/README.md`.
  - Komponenty: `useTranslations("obszar")`, daty i liczby przez `useFormatter()` z nazwanymi formatami z `src/i18n/formats.ts` (nie `Intl.DateTimeFormat("pl-PL")`). Tytuły stron: `export const generateMetadata = titled("klucz")`.
  - Funkcje w `src/lib/` zwracają klucze komunikatów (`knownCode(...)`), a komponent je tłumaczy; nie przekazuj gotowych zdań z bibliotek.
  - Treść (notatki, artykuły, etykiety atlasu, nazwa organizacji) nie jest tłumaczona.
  - Strażnicy: `src/i18n/messages.test.ts` (te same klucze i argumenty w obu językach), `src/i18n/hardcoded.test.ts` (polskie litery i dosłowne `aria-label`/`title`/`placeholder`), ESLint `i18next/no-literal-string`.
- **Testy Vitest:** `@testing-library/react` jest aliasem do `src/test/rtl.tsx`, więc każdy render ma katalog komunikatów (domyślnie polski). Test po angielsku: `import { setTestLocale } from "@/test/rtl"` i `setTestLocale("en")`. API mockuj przez `msw` (`renderWithClient` z `@/test/render`).
- **E2E:** selektory po rolach i nazwach dostępnych (po polsku — scenariusze działają w domyślnym języku). Scenariusz, który zmienia stan konta (język, blokada, branding), przywraca go w `finally`. Raporty Playwrighta (`playwright-report/`, `test-results/`) są wyłączone z lintu.

## 7. Zależności
- Wersje przypinaj dokładnie (`npm install --save-exact`, w `uv` przez `uv.lock`) i wybieraj wydania starsze niż dwa tygodnie.
- Nowa zależność musi mieć uzasadnienie w pliku etapu (sekcja *Changes against the plan* lub *Implementation*).

## 8. Znane Pułapki Środowiska Deweloperskiego
- **Użytkownicy deweloperscy:** `member1`, `member2`, `expert1`, `admin1`; hasło w `secrets/dev_user_password`, TOTP w `secrets/dev_totp_seed`. `make mfa-off` wyłącza OTP (wtedy test step-up jest pomijany).
- **Scenariusz E2E 4 (happy path) zależy od modeli:** jakość szkicu z modelu w chmurze i embeddingów decyduje, czy czat zacytuje artykuł. Porażka z odpowiedzią „Brak oficjalnego stanowiska…” przy szkicu bez treści to problem środowiska, nie regresja — sprawdź `error-context.md` w `frontend/test-results/`. Przy zdegenerowanym modelu embeddingów (`make smoke`) scenariusz jest pomijany.
- **Cache brandingu:** layout trzyma branding 10 s; test, który go zmienia, przeładowuje stronę aż zobaczy zmianę.
- **Przełącznik języka** zapisuje wybór na koncie dopiero po odpowiedzi `/me`; test klikający od razu po załadowaniu strony powinien poczekać na menu.
