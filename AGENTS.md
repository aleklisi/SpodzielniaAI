# Reguły Projektu: Spółdzielnia AI

## 1. Stos Technologiczny i Narzędzia
- **Język:** Python 3.12+ (zarządzany wyłącznie przez `uv`).
- **Backend:** FastAPI, SQLAlchemy 2.0 (async) + Alembic, Pydantic v2.
- **Bazy i Kolejki:** PostgreSQL 16 (z Row-Level Security), Qdrant, Redis + Celery.
- **AI & NLP:** LiteLLM, Docling, faster-whisper, Microsoft Presidio + spaCy (`pl_core_news_lg`), LLM-Guard.
- **Frontend:** Next.js (App Router), TypeScript, TailwindCSS, BlockNote/TipTap.

## 2. Komendy Weryfikacyjne (Uruchamiaj po każdej zmianie)
- Dodawanie pakietów: `uv add <pakiet>`
- Linting i formatowanie: `uv run ruff check --fix . && uv run ruff format .`
- Kontrola typów (obowiązkowa): `uv run pyright`
- Testy: `uv run pytest`

## 3. Żelazne Zasady Architektoniczne (Nienaruszalne)
1. **Izolacja (RLS):** Każde zapytanie do `PrivateNote` musi przechodzić przez sesję z ustawionym `app.current_user_id`. Nigdy nie polegaj wyłącznie na filtrach `WHERE` w kodzie Pythona.
2. **Block-Level Provenance:** Każdy blok tekstu (`ArticleBlock`) i odpowiadający mu wektor w Qdrant musi posiadać `author_id` oraz `consent_status` (`ACTIVE`, `SUSPENDED`, `REVOKED`).
3. **Anti-Distillation:** API nigdy nie zwraca surowych chunków z bazy Qdrant do klienta. Zwracana jest wyłącznie synteza z modelu LLM po przejściu przez `AntiDistillationGuard`.
4. **Dane wrażliwe tylko u dopuszczonych dostawców:** Anonimizacja (PII Scrubbing), prywatne notatki, szkice promocji, obrazy i embeddingi danych prywatnych trafiają wyłącznie do dostawców modeli dopuszczonych do danych prywatnych (lista operatora `LLM_PRIVATE_DATA_ENDPOINTS` w `.env` + zgoda ADMIN z MFA). Wymuszaj flagę `private_data=True`; brak dopuszczonego dostawcy wyłącza funkcję — nigdy nie przełączaj na innego dostawcę. (Zmiana z „tylko lokalny LLM”, Etap 10, US-04.)

## 4. Cykl Pracy (Workflow)
- Przed rozpoczęciem pracy przeczytaj `PLAN.md` i zidentyfikuj pierwszy niezrealizowany podpunkt `- [ ]`.
- Doczytaj szczegóły biznesowe z `docs/ARCHITECTURE.md` oraz wymogi bezpieczeństwa z `docs/SECURITY.md`.
- Napisz kod oraz testy jednostkowe/integracyjne.
- Uruchom `ruff`, `pyright` oraz `pytest`.
- Dopiero gdy wszystkie testy przechodzą na zielono, oznacz zadanie jako `- [x]` w `PLAN.md` i wykonaj git commit.
- NIE przechodź do kolejnego etapu bez wyraźnego polecenia użytkownika.