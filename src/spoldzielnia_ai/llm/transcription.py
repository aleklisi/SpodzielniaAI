"""Speech-to-text over the OpenAI-style ``/audio/transcriptions`` API (chunk 10.4, P9).

One multipart request per audio segment: ``file``, ``model`` and ``language``, nothing else,
so the same request works with OpenAI, Groq, Mistral (Voxtral), vLLM and self-hosted whisper
servers. The reply's ``text`` is required; ``segments`` (start/end/text) are used when a server
sends them. The bytes come from the worker's decrypted temp file; nothing is fetched by URL.

Cloud endpoints go through the egress proxy (7.3), local ones directly; redirects are not
followed. Errors map like the rest of the gateway: connection errors, timeouts, 5xx and
408/429 are ``ModelUnavailable`` (retry later), any other 4xx is ``ModelRejected``, a reply
without text is ``LLMOutputInvalid``. Neither the audio nor the text is logged.

Routing and the private-data policy are the gateway's (``LiteLLMGateway.transcribe``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import httpx

from spoldzielnia_ai.llm.gateway import LLMOutputInvalid, ModelRejected, ModelUnavailable

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.llm.gateway import Deployment

MAX_REPLY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Segment:
    """Seconds from the start of the audio the request carried (or, stitched, the file)."""

    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    segments: tuple[Segment, ...] = ()


def _segments(raw: object) -> tuple[Segment, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[Segment] = []
    for item in cast("list[Any]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        try:
            start, end = float(entry["start"]), float(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(entry.get("text") or "").strip()
        if text:
            out.append(Segment(start, end, text))
    return tuple(out)


def parse_reply(response: httpx.Response) -> Transcription:
    if len(response.content) > MAX_REPLY_BYTES:
        raise LLMOutputInvalid("transcription reply too large")
    if response.headers.get("content-type", "").startswith("text/plain"):
        return Transcription(response.text.strip())
    try:
        data: Any = response.json()
    except ValueError:
        raise LLMOutputInvalid("transcription reply is not JSON") from None
    if not isinstance(data, dict) or not isinstance(cast("dict[str, Any]", data).get("text"), str):
        raise LLMOutputInvalid("transcription reply has no text")
    body = cast("dict[str, Any]", data)
    return Transcription(str(body["text"]).strip(), _segments(body.get("segments")))


async def post_transcription(
    deployment: Deployment,
    audio: bytes,
    filename: str,
    *,
    language: str,
    settings: LlmSettings,
) -> Transcription:
    proxy = settings.egress_proxy_url if not deployment.local else None
    headers = {"Authorization": f"Bearer {deployment.api_key}"} if deployment.api_key else {}
    url = f"{deployment.base_url.rstrip('/')}/audio/transcriptions"
    async with httpx.AsyncClient(
        proxy=proxy,
        trust_env=False,
        follow_redirects=False,
        timeout=settings.transcribe_timeout_seconds,
    ) as client:
        try:
            response = await client.post(
                url,
                headers=headers,
                data={"model": deployment.model, "language": language},
                files={"file": (filename, audio, "audio/wav")},
            )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"{deployment.name}: {type(exc).__name__}") from exc
    status = response.status_code
    if status >= 500 or status in (408, 429):
        raise ModelUnavailable(f"{deployment.name}: HTTP {status}")
    if status != 200:
        # 3xx included: a redirect is not followed (it could point anywhere).
        raise ModelRejected(f"{deployment.name}: HTTP {status}")
    return parse_reply(response)
