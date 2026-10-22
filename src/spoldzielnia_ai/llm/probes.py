"""Connection tests and model probes for the admin console (7.4).

Synthetic inputs only (fixed Polish prompts, a generated image, two unrelated sentences): a probe
never touches notes, wiki content or anything a member wrote, so it can run against a cloud
model (when ``llm.allow_cloud`` allows it at all). Reports hold check names, pass/fail, timings,
token counts and error class names: never the prompt or the model's output.

- CHAT: a plain reply; a JSON answer with ``response_format`` (constrained decoding); the same in
  prompt mode. The report recommends the structured-output mode that worked (LM Studio + Bielik
  rejects ``json_schema`` with a 400, see 3.4).
- VLM: describe the colour of a generated 64x64 image.
- EMBED: the vector dimension (must equal ``qdrant.vector_size``) and whether two unrelated
  sentences end up apart (the smoke check of 6.7).
"""

from __future__ import annotations

import base64
import io
import math
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

import httpx
from pydantic import BaseModel

from spoldzielnia_ai.db.enums import LlmCapability, LlmProviderKind
from spoldzielnia_ai.llm.gateway import (
    Deployment,
    LiteLLMGateway,
    LLMError,
    LLMPurpose,
    ResolvedLlmConfig,
    StaticConfig,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.llm.gateway import ChatMessage, ChatTransport

MAX_MODELS_BODY = 1024 * 1024
MAX_LISTED_MODELS = 500
SEPARATION_MAX_COSINE = 0.9


class CityAnswer(BaseModel):
    city: str
    country: str


SYSTEM: ChatMessage = {"role": "system", "content": "Odpowiadasz krótko, po polsku."}
REPLY: ChatMessage = {"role": "user", "content": "Podaj stolicę Polski jednym słowem."}
JSON_TASK: ChatMessage = {
    "role": "user",
    "content": "Zwróć dane: miasto Kraków, kraj Polska (pola city i country).",
}
UNRELATED = ("Pszczoły zbierają nektar z kwitnącej lipy.", "Bank podniósł oprocentowanie kredytów.")


def deployment_for(
    name: str,
    model: str,
    base_url: str,
    api_key: str | None,
    kind: LlmProviderKind,
    *,
    private_data: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    structured_output: str | None = None,
    timeout_seconds: float | None = None,
) -> Deployment:
    return Deployment(
        name=name,
        model=model,
        base_url=base_url,
        api_key=api_key,
        local=kind == LlmProviderKind.LOCAL,
        private_data=private_data,
        temperature=temperature,
        max_tokens=max_tokens,
        structured_output=structured_output
        if structured_output in ("json_schema", "prompt")
        else None,  # pyright: ignore[reportArgumentType]
        timeout_seconds=timeout_seconds,
    )


def single_gateway(
    deployment: Deployment, settings: LlmSettings, transport: ChatTransport | None = None
) -> LiteLLMGateway:
    """A gateway that knows one deployment, reachable only under ``PLAYGROUND`` (synthetic
    prompts; embedding probes too). The usual checks still apply: no cloud without
    ``allow_cloud``, and private presets only on cleared deployments."""
    routes = {LLMPurpose.PLAYGROUND: deployment.name}
    config = ResolvedLlmConfig(-1, {deployment.name: deployment}, routes)
    return LiteLLMGateway(settings, transport, source=StaticConfig(config))


async def _timed(name: str, work: Any) -> tuple[dict[str, JsonValue], Any]:
    started = time.monotonic()
    try:
        result = await work
    except LLMError as exc:
        cause = exc.__cause__
        return {
            "name": name,
            "ok": False,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": type(exc).__name__,
            "cause": type(cause).__name__ if cause is not None else None,
        }, None
    return {"name": name, "ok": True, "latency_ms": round((time.monotonic() - started) * 1000)}, (
        result
    )


def _tokens(result: Any) -> int | None:
    usage: dict[str, int] = getattr(result, "usage", {}) or {}
    return usage.get("total_tokens")


async def _probe_chat(
    deployment: Deployment, settings: LlmSettings, transport: ChatTransport | None
) -> dict[str, JsonValue]:
    checks: list[dict[str, JsonValue]] = []
    gateway = single_gateway(deployment, settings, transport)
    check, result = await _timed(
        "reply", gateway.complete([SYSTEM, REPLY], purpose=LLMPurpose.PLAYGROUND)
    )
    if result is not None:
        text = str(result.content).strip()
        check["ok"] = bool(text)
        check["tokens"] = _tokens(result)
        check["polish_answer"] = "warszaw" in text.lower()
    checks.append(check)
    modes: dict[str, bool] = {}
    for mode in ("json_schema", "prompt"):
        variant = single_gateway(replace(deployment, structured_output=mode), settings, transport)  # pyright: ignore[reportArgumentType]
        check, result = await _timed(
            f"json_{mode}",
            variant.complete(
                [SYSTEM, JSON_TASK], purpose=LLMPurpose.PLAYGROUND, response_model=CityAnswer
            ),
        )
        if result is not None:
            check["tokens"] = _tokens(result)
            check["attempts"] = result.attempts
        modes[mode] = bool(check["ok"])
        checks.append(check)
    recommended = "json_schema" if modes["json_schema"] else "prompt" if modes["prompt"] else None
    ok = bool(checks[0]["ok"]) and recommended is not None
    listed: list[JsonValue] = list(checks)
    return {"ok": ok, "checks": listed, "recommended_structured_output": recommended}


def probe_image_data_url() -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 30, 30)).save(buffer, "PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


async def _probe_vlm(
    deployment: Deployment, settings: LlmSettings, transport: ChatTransport | None
) -> dict[str, JsonValue]:
    gateway = single_gateway(deployment, settings, transport)
    message: ChatMessage = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Jaki kolor wypełnia ten obrazek? Odpowiedz jednym słowem."},
            {"type": "image_url", "image_url": {"url": probe_image_data_url()}},
        ],
    }
    check, result = await _timed(
        "image", gateway.complete([message], purpose=LLMPurpose.PLAYGROUND)
    )
    if result is not None:
        text = str(result.content).lower()
        check["ok"] = bool(text.strip())
        check["tokens"] = _tokens(result)
        check["colour_named"] = "czerw" in text or "red" in text
    return {"ok": bool(check["ok"]), "checks": [check]}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 1.0


async def _probe_embed(
    deployment: Deployment,
    settings: LlmSettings,
    transport: ChatTransport | None,
    vector_size: int | None,
) -> dict[str, JsonValue]:
    # Synthetic sentences only, so any allowed provider may be probed before it is cleared.
    gateway = single_gateway(deployment, settings, transport)
    check, vectors = await _timed(
        "embed", gateway.embed(list(UNRELATED), private_data=False, purpose=LLMPurpose.PLAYGROUND)
    )
    report: dict[str, JsonValue] = {"ok": False, "checks": [check]}
    if vectors is None:
        return report
    dimension = len(vectors[0])
    similarity = round(_cosine(vectors[0], vectors[1]), 4)
    check["dimension"] = dimension
    check["cosine_unrelated"] = similarity
    separates = similarity < SEPARATION_MAX_COSINE
    matches = vector_size is None or dimension == vector_size
    check["ok"] = dimension > 0 and separates and matches
    report |= {
        "ok": check["ok"],
        "dimension": dimension,
        "dimension_matches": matches,
        "expected_dimension": vector_size,
        "separates_unrelated": separates,
    }
    return report


async def probe_model(
    capability: LlmCapability,
    deployment: Deployment,
    settings: LlmSettings,
    *,
    vector_size: int | None = None,
    transport: ChatTransport | None = None,
) -> dict[str, JsonValue]:
    if capability == LlmCapability.VLM:
        return await _probe_vlm(deployment, settings, transport)
    if capability == LlmCapability.EMBED:
        return await _probe_embed(deployment, settings, transport, vector_size)
    return await _probe_chat(deployment, settings, transport)


async def list_models(
    base_url: str,
    api_key: str | None,
    kind: LlmProviderKind,
    settings: LlmSettings,
    client: httpx.AsyncClient | None = None,
) -> dict[str, JsonValue]:
    """``GET {base_url}/models``: reachability, authentication and the model ids on offer.
    Cloud endpoints go through the egress proxy; redirects are not followed."""
    proxy = settings.egress_proxy_url if kind == LlmProviderKind.CLOUD else None
    own = client is None
    http = client or httpx.AsyncClient(
        proxy=proxy, trust_env=False, follow_redirects=False, timeout=10.0
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    started = time.monotonic()
    try:
        async with http.stream("GET", f"{base_url}/models", headers=headers) as response:
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_MODELS_BODY:
                    return {"ok": False, "status": response.status_code, "error": "too_large"}
            latency = round((time.monotonic() - started) * 1000)
            if response.status_code != 200:
                return {"ok": False, "status": response.status_code, "latency_ms": latency}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__}
    finally:
        if own:
            await http.aclose()
    try:
        data: Any = httpx.Response(200, content=body).json()
        items = (
            cast("list[Any]", cast("dict[str, Any]", data).get("data", []))
            if isinstance(data, dict)
            else []
        )
        ids = sorted(
            {str(cast("Any", i)["id"])[:256] for i in items if isinstance(i, dict) and "id" in i}
        )
    except (ValueError, TypeError):
        return {"ok": False, "status": 200, "latency_ms": latency, "error": "invalid_json"}
    models: list[JsonValue] = list(ids[:MAX_LISTED_MODELS])
    return {"ok": True, "status": 200, "latency_ms": latency, "models": models}
