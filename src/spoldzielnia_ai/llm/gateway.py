"""LLM gateway (chunk 3.4): one place that decides where a prompt may go.

Endpoints speak the standard OpenAI API (LM Studio, Ollama, vLLM …), reached through
LiteLLM's ``openai/`` provider with a configurable ``api_base``. Deployments: ``local-chat``,
``local-vlm``, ``local-embed`` and an optional ``cloud-chat``.

Routing policy (AGENTS.md §3.4):

- Purposes in ``ALWAYS_LOCAL`` (PII scrubbing, private notes, promotion, vision, embeddings)
  are local **whatever the caller passes**. That is defence in depth against a missing
  ``force_local_llm=True``.
- ``force_local_llm=True`` → local deployments only. If none is configured, or the call to it
  fails, ``LocalModelUnavailable`` is raised. **There is never a fallback to the cloud.**
- The cloud is used only if ``llm.allow_cloud`` is set, for purposes outside
  ``ALWAYS_LOCAL`` and without ``force_local_llm``.

Structured output: ``response_model`` (pydantic) is sent as a JSON schema, and the reply is
validated. An invalid reply gets one corrective retry, then ``LLMOutputInvalid``. With
``llm.structured_output = "prompt"`` the schema goes into the system prompt instead of
``response_format``: some local servers can't constrain output for some models (LM Studio's
grammar sampler fails on Bielik's chat template with a 400), and the validation and retry stay
the same.

Failures: connection errors, timeouts, 5xx and 408/429 are ``LocalModelUnavailable`` (worth
retrying later). Any other 4xx from a local server is ``LocalModelRejected``: the same request
would fail again.
No prompt or completion is logged, and LiteLLM's callbacks, telemetry and cost-map download are
off.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, cast, overload

from pydantic import BaseModel, ValidationError

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import LlmSettings

# Before LiteLLM is imported anywhere: never download the model price map at runtime.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

log = get_logger(__name__)


class LLMPurpose(StrEnum):
    PII_SCRUB = "PII_SCRUB"
    PRIVATE_NOTE = "PRIVATE_NOTE"
    PROMOTION = "PROMOTION"
    VLM = "VLM"
    EMBED = "EMBED"
    RAG = "RAG"  # Stage 5: canonical (public) knowledge only


ALWAYS_LOCAL = frozenset(
    {
        LLMPurpose.PII_SCRUB,
        LLMPurpose.PRIVATE_NOTE,
        LLMPurpose.PROMOTION,
        LLMPurpose.VLM,
        LLMPurpose.EMBED,
    }
)

LOCAL_CHAT, LOCAL_VLM, LOCAL_EMBED, CLOUD_CHAT = (
    "local-chat",
    "local-vlm",
    "local-embed",
    "cloud-chat",
)


class LLMError(Exception):
    pass


class LocalModelUnavailable(LLMError):
    """No local deployment for a purpose that must stay on the server (or it failed)."""


class LocalModelRejected(LLMError):
    """The local server refused the request (4xx): retrying the same request won't help."""


class LLMOutputInvalid(LLMError):
    """The model did not produce output matching the required schema (after one retry)."""


class TextPart(TypedDict):
    type: Literal["text"]
    text: str


class ImageUrl(TypedDict):
    url: str  # data: URL — images never leave the server as links


class ImagePart(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrl


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str | list[TextPart | ImagePart]


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    content: str
    parsed: T | None
    deployment: str
    model: str
    attempts: int = 1
    usage: dict[str, int] = field(default_factory=dict[str, int])


class LLMGateway(Protocol):
    @overload
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        force_local_llm: bool = False,
        response_model: None = None,
    ) -> LLMResult[BaseModel]: ...

    @overload
    async def complete[T: BaseModel](
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        force_local_llm: bool = False,
        response_model: type[T],
    ) -> LLMResult[T]: ...

    async def embed(
        self, texts: list[str], *, force_local_llm: bool = True
    ) -> list[list[float]]: ...


class ChatTransport(Protocol):
    """What the gateway needs from LiteLLM's Router (tests substitute a fake)."""

    async def acompletion(self, model: str, messages: list[Any], **kwargs: Any) -> Any: ...

    async def aembedding(self, model: str, input: list[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class Deployment:
    name: str
    model: str
    base_url: str
    api_key: str | None
    local: bool


def deployments(settings: LlmSettings) -> dict[str, Deployment]:
    # An empty secret file means "no token".
    key = settings.api_key.get_secret_value().strip() or None if settings.api_key else None
    found: dict[str, Deployment] = {}
    for name, model in (
        (LOCAL_CHAT, settings.chat_model),
        (LOCAL_VLM, settings.vlm_model),
        (LOCAL_EMBED, settings.embed_model),
    ):
        if model:
            found[name] = Deployment(name, model, settings.local_base_url, key, local=True)
    if settings.allow_cloud and settings.cloud_model and settings.cloud_base_url:
        cloud_key = settings.cloud_api_key.get_secret_value() if settings.cloud_api_key else None
        found[CLOUD_CHAT] = Deployment(
            CLOUD_CHAT, settings.cloud_model, settings.cloud_base_url, cloud_key, local=False
        )
    return found


def litellm_router(settings: LlmSettings) -> ChatTransport:
    """LiteLLM Router over the configured deployments (OpenAI-compatible, no fallbacks)."""
    import litellm
    from litellm.router import Router

    litellm.turn_off_message_logging = True
    # httpx (like the rest of the stack) instead of aiohttp.
    litellm.disable_aiohttp_transport = True
    litellm.suppress_debug_info = True
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm.callbacks = []
    model_list: list[dict[str, Any]] = [
        {
            "model_name": d.name,
            "litellm_params": {
                "model": f"openai/{d.model}",
                "api_base": d.base_url,
                "api_key": d.api_key or "not-needed",
                "timeout": settings.timeout_seconds,
            },
        }
        for d in deployments(settings).values()
    ]
    return cast("ChatTransport", Router(model_list=model_list, num_retries=1, fallbacks=[]))  # pyright: ignore[reportArgumentType]


def _json_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": model.model_json_schema(),
            "strict": True,
        },
    }


_FENCED = re.compile(r"```(?:json)?[ \t]*\n(.*?)\n?```", re.DOTALL)


def _extract_json(text: str) -> str:
    """Local models often wrap JSON in ```json fences or add a sentence around it (always
    without a schema constraint). Take the fenced block, else the outermost ``{…}``."""
    stripped = text.strip()
    if fenced := _FENCED.search(stripped):
        return fenced.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start > 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def _schema_in_prompt(messages: list[ChatMessage], model: type[BaseModel]) -> list[ChatMessage]:
    """``structured_output = "prompt"``: append the schema to the (first) system message.
    One system message only, as some chat templates reject a second one."""
    schema = json.dumps(model.model_json_schema(), ensure_ascii=False)
    note = f"\n\nSchemat JSON odpowiedzi (zwróć wyłącznie obiekt JSON zgodny z nim):\n{schema}"
    out = list(messages)
    first = out[0] if out else None
    if first is not None and first["role"] == "system" and isinstance(first["content"], str):
        out[0] = {"role": "system", "content": first["content"] + note}
    else:
        out.insert(0, {"role": "system", "content": note.strip()})
    return out


def _rejected(exc: Exception) -> bool:
    """A 4xx other than 408/429: the server understood and refused the request."""
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and 400 <= code < 500 and code not in (408, 429)


class LiteLLMGateway:
    def __init__(self, settings: LlmSettings, transport: ChatTransport | None = None) -> None:
        self._settings = settings
        self._deployments = deployments(settings)
        self._transport = transport

    def _router(self) -> ChatTransport:
        if self._transport is None:
            self._transport = litellm_router(self._settings)
        return self._transport

    def route(self, purpose: LLMPurpose, force_local_llm: bool) -> Deployment:
        local_only = force_local_llm or purpose in ALWAYS_LOCAL
        wanted = (
            LOCAL_VLM
            if purpose == LLMPurpose.VLM
            else LOCAL_EMBED
            if purpose == LLMPurpose.EMBED
            else LOCAL_CHAT
        )
        if wanted in self._deployments:
            return self._deployments[wanted]
        if local_only:
            raise LocalModelUnavailable(f"no local deployment for {purpose.value}")
        if CLOUD_CHAT in self._deployments and purpose not in (LLMPurpose.VLM, LLMPurpose.EMBED):
            return self._deployments[CLOUD_CHAT]
        raise LLMError(f"no deployment for {purpose.value}")

    @overload
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        force_local_llm: bool = False,
        response_model: None = None,
    ) -> LLMResult[BaseModel]: ...

    @overload
    async def complete[T: BaseModel](
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        force_local_llm: bool = False,
        response_model: type[T],
    ) -> LLMResult[T]: ...

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        purpose: LLMPurpose,
        force_local_llm: bool = False,
        response_model: type[BaseModel] | None = None,
    ) -> LLMResult[Any]:
        deployment = self.route(purpose, force_local_llm)
        kwargs: dict[str, Any] = {
            "max_tokens": self._settings.max_tokens,
            "temperature": self._settings.temperature,
        }
        if response_model is not None and self._settings.structured_output == "prompt":
            messages = _schema_in_prompt(messages, response_model)
        elif response_model is not None:
            kwargs["response_format"] = _json_schema(response_model)
        conversation: list[Any] = list(messages)
        for attempt in (1, 2):
            try:
                response = await self._router().acompletion(deployment.name, conversation, **kwargs)
            except Exception as exc:  # connection refused, timeout, 5xx …
                rejected = _rejected(exc)
                log.warning(
                    "llm.call_failed",
                    deployment=deployment.name,
                    error=type(exc).__name__,
                    status=getattr(exc, "status_code", None),
                    hint="try llm.structured_output=prompt"
                    if rejected and "response_format" in kwargs
                    else None,
                )
                if deployment.local and rejected:
                    raise LocalModelRejected(f"{deployment.name}: {type(exc).__name__}") from exc
                if deployment.local:
                    raise LocalModelUnavailable(f"{deployment.name}: {type(exc).__name__}") from exc
                raise LLMError(f"{deployment.name}: {type(exc).__name__}") from exc
            content = str(response.choices[0].message.content or "")
            usage = _usage(response)
            if response_model is None:
                return LLMResult(content, None, deployment.name, deployment.model, attempt, usage)
            try:
                parsed = response_model.model_validate_json(_extract_json(content))
            except ValidationError as exc:
                log.warning("llm.invalid_output", deployment=deployment.name, attempt=attempt)
                conversation = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Odpowiedź nie spełnia schematu JSON. Zwróć wyłącznie poprawny "
                        f"JSON zgodny ze schematem. Błędy: {_errors(exc)}",
                    },
                ]
                continue
            return LLMResult(content, parsed, deployment.name, deployment.model, attempt, usage)
        schema = response_model.__name__ if response_model is not None else "text"
        raise LLMOutputInvalid(f"{deployment.name}: output does not match {schema}")

    async def embed(self, texts: list[str], *, force_local_llm: bool = True) -> list[list[float]]:
        deployment = self.route(LLMPurpose.EMBED, force_local_llm)
        try:
            response = await self._router().aembedding(deployment.name, input=texts)
        except Exception as exc:
            raise LocalModelUnavailable(f"{deployment.name}: {type(exc).__name__}") from exc
        return [list(map(float, item["embedding"])) for item in response.data]


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: int(getattr(usage, key, 0) or 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _errors(exc: ValidationError) -> str:
    """Error locations and types only (no input values echoed back into the prompt)."""
    return json.dumps([{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()][:10])
