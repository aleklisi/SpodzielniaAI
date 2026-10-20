"""The admin's model playground (7.5): one prompt, up to three models, side by side.

Only what the admin types or a fixed preset reaches the models: this module never retrieves
(no Qdrant, wiki, notes or shield; an import-linter contract keeps it that way), so it cannot
become a path around the anti-distillation shield (AGENTS.md §3.3). Answers go back to the
admin and are not stored; the audit log gets a hash of the prompt, the preset and the models.

Presets with (synthetic) personal data are local-only, like the purposes they stand in for:
the playground must not teach anyone to paste such text into a cloud model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from spoldzielnia_ai.llm.gateway import LLMError, LLMPurpose

if TYPE_CHECKING:
    from pydantic import JsonValue

    from spoldzielnia_ai.llm.gateway import ChatMessage, Deployment, LiteLLMGateway

MAX_PROMPT = 4000
MAX_ANSWER = 8000


class NoteFacts(BaseModel):
    date: str | None
    apiary: str | None
    actions: list[str]


@dataclass(frozen=True, slots=True)
class Preset:
    key: str
    title: str
    prompt: str
    local_only: bool = False
    response_model: type[BaseModel] | None = None


SYNTHETIC_NOTE = (
    "12 maja 2026, pasieka Lipowa Dolina. Przegląd 8 uli, w ulu nr 3 matka nieczerwiąca - "
    "wymiana zaplanowana. Dokarmianie syropem 2 l. Kontakt do weterynarza: Jan Testowy, "
    "PESEL 44051401359, tel. 600 100 200."
)

PRESETS: dict[str, Preset] = {
    p.key: p
    for p in (
        Preset(
            "pl_qa",
            "Pytanie merytoryczne",
            "Wyjaśnij w trzech zdaniach, czym jest warroza i jak pszczelarze ją zwalczają.",
        ),
        Preset(
            "pl_style",
            "Styl i poprawność",
            "Przepisz formalnie, poprawiając błędy: 'ule byly mocne ale w trzecim matka "
            "chyba padła bo nie ma czerwiu i trzeba bedzie cos z tym zrobic'.",
        ),
        Preset(
            "json_extract",
            "Ekstrakcja do JSON",
            "Z notatki wypisz datę, nazwę pasieki i wykonane czynności (pola date, apiary, "
            "actions). Notatka: 12 maja, pasieka Lipowa Dolina, przegląd 8 uli, dokarmianie "
            "syropem.",
            response_model=NoteFacts,
        ),
        Preset(
            "summary_pii",
            "Streszczenie notatki z danymi osobowymi (syntetycznymi)",
            f"Streść notatkę w dwóch zdaniach, bez danych osobowych: {SYNTHETIC_NOTE}",
            local_only=True,
        ),
    )
}

SYSTEM: ChatMessage = {
    "role": "system",
    "content": "Jesteś asystentem spółdzielni pszczelarskiej. Odpowiadasz po polsku.",
}


def prompt_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def run_one(
    gateway: LiteLLMGateway, deployment: Deployment, prompt: str, preset: Preset | None
) -> dict[str, JsonValue]:
    started = time.monotonic()
    result: dict[str, JsonValue] = {"name": deployment.name, "local": deployment.local}
    response_model = preset.response_model if preset else None
    try:
        reply: Any = await gateway.complete(
            [SYSTEM, {"role": "user", "content": prompt}],
            purpose=LLMPurpose.PLAYGROUND,
            force_local_llm=bool(preset and preset.local_only),
            response_model=response_model,
        )
    except LLMError as exc:
        return result | {
            "ok": False,
            "error": type(exc).__name__,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    content = str(reply.content)
    json_valid: bool | None = None
    if response_model is not None:
        json_valid = reply.parsed is not None
    else:
        try:
            json.loads(content)
        except ValueError:
            json_valid = None
        else:
            json_valid = True
    return result | {
        "ok": True,
        "content": content[:MAX_ANSWER],
        "truncated": len(content) > MAX_ANSWER,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "tokens": reply.usage.get("total_tokens"),
        "attempts": reply.attempts,
        "json_valid": json_valid,
    }


async def compare(
    runs: list[tuple[LiteLLMGateway, Deployment]], prompt: str, preset: Preset | None
) -> list[dict[str, JsonValue]]:
    return list(await asyncio.gather(*(run_one(g, d, prompt, preset) for g, d in runs)))
