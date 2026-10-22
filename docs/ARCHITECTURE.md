## Specyfikacja Wymagań (SRS) – Platforma „Spółdzielnia AI”

### 1. Architektura Systemu (Model Centralny)

* **Wspólny serwer centralny:** Całość infrastruktury (baza dokumentów, baza wektorowa, storage multimediów, kolejki przetwarzania) działa na jednej, centralnej instancji serwerowej spółdzielni.
* **Ścisła izolacja logiczna (Multi-tenancy):**
* **Strefa Prywatna (Notatniki):** Zaszyfrowane przestrzenie indywidualnych członków. Brak dostępu dla innych użytkowników, Rady Ekspertów oraz globalnego silnika RAG.
* **Strefa Wspólna (Wiki + Globalny RAG):** Przestrzeń współdzielona, zasilana wyłącznie poprzez świadome wypchnięcie danych przez autora.


* **Hybrydowy Router LLM (Centralny Gateway):**
* Każdy model jest dostępny przez API (standard OpenAI): lokalnie (np. LM Studio, Ollama, vLLM na sprzęcie spółdzielni) lub w chmurze. Administrator wybiera model dla każdego zastosowania w panelu `/admin/llm` (Etap 7, Etap 10).
* Transkrypcja, OCR, anonimizacja, opisy zdjęć, embeddingi i zapytania z prywatnych notatników trafiają wyłącznie do dostawców **dopuszczonych do danych prywatnych**: adres musi być na liście operatora serwera, a dopuszczenie (region, przechowywanie, umowa) zatwierdza administrator z MFA. Może to być serwer spółdzielni albo dostawca chmurowy z umową powierzenia danych.
* Pozostałe modele zewnętrzne (API chmurowe) są wywoływane opcjonalnie, tylko dla wiedzy kanonicznej, zgodnie z polityką bezpieczeństwa danej organizacji.



---

### 2. Przepływ Wiedzy i Rozwiązywanie Sprzeczności (Workflow)

* **Jednokierunkowa publikacja (Push-to-Wiki):**
* W prywatnym notatniku użytkownik zaznacza wybrane notatki/multimedia i uruchamia akcję **„Utwórz wniosek do Wiki”**.
* System lokalnym modelem LLM generuje z surowych notatek ustrukturyzowany szkic artykułu, czyszcząc go z danych wrażliwych (PII), i przekazuje do kolejki Rady Ekspertów.


* **Zarządzanie sprzecznościami (Zasada jednej prawdy):**
* Do globalnego RAG trafiają **wyłącznie** artykuły zatwierdzone przez Radę Ekspertów (status: *Kanoniczny*).
* W przypadku pojawienia się nowej wiedzy sprzecznej z dotychczasową (np. zmiana przepisów podatkowych lub nowa metoda leczenia warrozy), Rada Ekspertów zatwierdza nowy artykuł i oznacza poprzedni jako *Zastąpiony (Superseded)*.
* Oznaczenie dokumentu jako *Zastąpiony* lub *Sporny* natychmiast usuwa jego wektory z aktywnego indeksu RAG – LLM odpowiada wyłącznie na podstawie aktualnie obowiązującej wersji eksperckiej.



---

### 3. Zarządzanie Własnością Intelektualną i Prawo do Wycofania (Exit Strategy)

Ze względu na wymóg możliwości wstrzymania lub usunięcia własnego wkładu przez twórcę w dowolnym momencie, system musi realizować **śledzenie pochodzenia danych na poziomie blokowym (Block-level Provenance)**:

* **Metadane własności (Data Lineage):**
* Każdy artykuł, akapit (blok tekstu) oraz wygenerowany z niego wektor (chunk) w bazie RAG posiada twarde powiązanie w bazie danych: `author_id` oraz `consent_status` (*Active*, *Suspended*, *Revoked*).


* **Panel zarządzania wkładem twórcy:**
* Użytkownik posiada widok wszystkich swoich kontrybucji do wspólnej bazy z przełącznikami:
* **Wstrzymaj wykorzystanie (Suspend):** Wektory powiązane z wkładem autora otrzymują flagę `is_active = false`. Silnik RAG natychmiast przestaje je uwzględniać w odpowiedziach, a w Wiki treść jest ukrywana. Operacja jest odwracalna.
* **Usuń trwale (Revoke / Exit):** Trwałe usunięcie kontrybucji z bazy dokumentów oraz kaskadowe usunięcie odpowiadających jej wektorów z bazy RAG (Hard Delete).




* **Obsługa dokumentów kolaboracyjnych po wycofaniu zgody:**
* Jeśli artykuł w Wiki był pisany przez kilku autorów, a jeden z nich wycofa swój wkład, system automatycznie wycina wyłącznie bloki należące do tego autora.
* Artykuł pozbawiony części treści automatycznie traci status *Kanoniczny*, wypada z RAG i trafia z powrotem do Rady Ekspertów z alertem: *„Wymagana rewizja – wycofano część wkładu autorskiego”*.



---

### 4. Wymagania dla Silnika RAG i Normalizacji Danych

* **Potok przetwarzania (Ingestion Pipeline):**
* Przetwarzanie wsadowe multimediów w prywatnych notatnikach: Whisper (audio ➔ tekst), modele VLM (zdjęcia ➔ opis ustrukturyzowany), parsery dokumentów (PDF/DOCX ➔ Markdown).
* Dwa niezależne indeksy wektorowe na serwerze:
* `Private_Index` (partycjonowany po `user_id` – użytkownik odpytuje własny notatnik).
* `Canonical_Cooperative_Index` (wspólny – zawiera tylko zweryfikowaną wiedzę z `consent_status == Active`).




* **Wymagania dla odpowiedzi LLM:**
* **Ścisłe uziemienie (Strict Grounding):** Model odpowiada na pytania w trybie spółdzielczym wyłącznie na podstawie `Canonical_Cooperative_Index`. Jeśli w zweryfikowanej bazie brakuje odpowiedzi, system komunikuje brak oficjalnego stanowiska ekspertów (zamiast halucynować z wiedzy ogólnej modelu).
* **Cytowania:** Każda odpowiedź musi zawierać bezpośredni link do artykułu w Wiki, datę jego ostatniej weryfikacji oraz listę autorów/ekspertów zatwierdzających.



---

### 5. Proponowany Stos Technologiczny (Open Source / Self-Hosted)

* **Backend i Orkiestracja RAG:** Python (FastAPI, LangChain / LlamaIndex) + Celery (kolejkowanie zadań transkrypcji i indeksowania).
* **Baza Wektorowa (z obsługą filtrowania po metadanych i partycjonowania):** Qdrant lub Milvus (pozwalają na błyskawiczne wyłączanie wektorów po `author_id` bez konieczności przeindeksowywania całej bazy).
* **Baza Relacyjna i Dokumentowa:** PostgreSQL (uprawnienia, wersjonowanie bloków tekstu, historia zgód, obieg dokumentów).
* **Lokalny Serwer Modeli:** vLLM lub Ollama (obsługa modeli językowych, np. Bielik / Llama 3, oraz modeli wizyjnych i Whispera).
* **Frontend:** Edytor blokowy (np. oparty na BlockNote / TipTap / ProseMirror) zapewniający przypisanie każdego akapitu do konkretnego autora.