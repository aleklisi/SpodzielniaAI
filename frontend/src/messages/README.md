# Message catalogues (Stage 14, US-08)

One JSON file per area and language: `pl/<area>.json` and `en/<area>.json`, ICU message syntax
(`{count, plural, one {…} few {…} many {…} other {…}}` for Polish plurals). `index.ts` imports
them; after adding an area, add it there for both languages. `src/i18n/messages.test.ts` checks
that both languages have the same keys and arguments; `src/i18n/hardcoded.test.ts` and ESLint
(`i18next/no-literal-string`) keep text out of the code.

Content is never translated: notes, wiki articles, atlas labels and domains, the organisation's
name, audit details. The server's own texts live in `src/spoldzielnia_ai/i18n.py`.

## Glossary

Use these English terms everywhere (British spelling: organisation, colour, recognise).

| Polski | English |
|---|---|
| Spółdzielnia | the cooperative |
| Rada Ekspertów | Expert Council |
| Notatnik | Notebook |
| Wiki, Baza wiedzy | Wiki, Knowledge base |
| Atlas | Atlas |
| Wniosek do Wiki | wiki proposal |
| Szkic | draft |
| Kanoniczny | canonical |
| Recenzja | review |
| Zgoda (wstrzymaj, wznów, wycofaj) | consent (suspend, resume, revoke) |
| Kontrybucje | contributions |
| Oznaczenie, etykieta, dziedzina | mark, label, domain |
| Rozpoznaj | Recognise |
| Twoje dane | Your data |
| Wygląd | Appearance |
| Modele AI, dostawca, przypisania | AI models, provider, assignments |
| dopuszczony do danych prywatnych | cleared for private data |
| Tarcza (anty-destylacyjna) | (anti-distillation) shield |
| kanarek | canary |
| Członek spółdzielni | Cooperative member |
