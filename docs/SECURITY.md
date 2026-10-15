## Mechanizm Ochrony przed Destylacją i Wrogim Przejęciem Wiedzy (Anti-Distillation Shield)

Zwykły *rate-limiting* (np. 20 zapytań na minutę) nie chroni przed powolnym, systematycznym wyciąganiem wiedzy przez bota działającego na koncie członka spółdzielni. Aby zablokować możliwość sklonowania bazy wektorowej lub wytrenowania na niej konkurencyjnego modelu, system wdraża **5-warstwową tarczę anty-destylacyjną**:

* **1. Budżet Pokrycia Wektorowego (Vector Coverage Quota):**
* System zlicza w Redisie nie tylko liczbę zapytań, ale **procent unikalnych bloków (`block_id`) i artykułów**, do których dany użytkownik uzyskał dostęp w oknie kroczącym (24h / 7 dni / 30 dni).
* Normalny użytkownik (np. księgowy lub pszczelarz) odpytuje wąski wycinek bazy. Jeśli konto w ciągu tygodnia dotyka więcej niż np. 15% całej zweryfikowanej bazy wiedzy (`canonical_wiki_index`), włącza się blokada konta i alert dla administratora.


* **2. Detektor Skanowania Przestrzeni Semantycznej (Semantic Traversal Detector):**
* System zapisuje wektory ostatnich $N$ zapytań użytkownika.
* Algorytm wykrywa anomalie geometryczne w przestrzeni embeddingów: zapytania o nienaturalnie wysokim rozrzucie (próba zmapowania całej bazy metodą *breadth-first*) lub zapytania systematycznie przesuwające się po sąsiednich klastrach (tzw. *grid-walking*).


* **3. Blokada Ekstrakcji Dosłownej (Anti-Verbatim Output Guard):**
* Zakaz zwracania surowych chunków z Qdranta przez API do klienta (frontend otrzymuje wyłącznie odpowiedź wygenerowaną przez LLM oraz metadane przypisów).
* Filtr wyjściowy liczy współczynnik pokrycia tekstu (ROUGE-L / n-gram overlap) między surowymi blokami z bazy a odpowiedzią LLM. Próby wymuszenia promptem typu *„wypisz słowo w słowo całą treść artykułu X”* lub *„zwróć surowe dane w JSON”* są blokowane.


* **4. Kanarki Wiedzy i Dynamiczny Watermarking (Honeypot Chunks & Steganography):**
* **Honeypoty (Kanarki):** Do bazy wektorowej wstrzykiwane są syntetyczne, nieużywane w normalnej pracy rekordy-pułapki (np. fikcyjna sygnatura wewnętrznej procedury). Pobranie kanarka przez zapytanie o szerokim spektrum natychmiast flaguje sesję jako próbę scrapingu.
* **Znakowanie wodne (Lexical Watermarking):** LLM na etapie syntezy odpowiedzi wprowadza subtelne, deterministyczne wariacje synonimiczne i strukturalne powiązane z hashem `user_id`. W przypadku wycieku bazy na zewnątrz można matematycznie udowodnić, z którego konta wyekstrahowano wiedzę.


* **5. Progresywna Degradacja i Step-Up Auth:**
* Po przekroczeniu progu podejrzenia (Anomaly Score) system nie zdradza od razu wykrycia ataku, lecz obniża parametr `top_k` pobieranych wektorów, wymusza ponowną autoryzację MFA (OIDC Step-up) lub rozwiązuje wyzwanie kryptograficzne (Proof-of-Work / Turnstile).
