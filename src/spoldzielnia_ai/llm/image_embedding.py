"""Image embeddings over an OpenAI-style ``/embeddings`` API (chunk 10.6, P1/P8).

One request per batch: ``{"model", "input": [data URIs], "modality": "image",
"encoding_format": "float"}``, the shape of self-hosted embedding servers that serve CLIP/SigLIP
models (e.g. Infinity). Images are inline JPEG ``data:`` URIs, never links. Vectors are
L2-normalised here, so cosine similarity is a dot product as with the built-in SigLIP. Multimodal
embedding APIs are not standardised: a server that expects another shape fails the model probe
(which an activation requires), never silently.

Cloud endpoints go through the egress proxy (7.3), no redirects; errors map like the gateway's.
Routing and the private-data policy are ``LiteLLMGateway.embed_images``'s.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

import httpx

from spoldzielnia_ai.llm.gateway import LLMOutputInvalid, ModelRejected, ModelUnavailable

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import LlmSettings
    from spoldzielnia_ai.llm.gateway import Deployment

MAX_REPLY_BYTES = 64 * 1024 * 1024


def _normalised(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if not norm:
        raise LLMOutputInvalid("zero image embedding")
    return [x / norm for x in vector]


def parse_vectors(response: httpx.Response, count: int) -> list[list[float]]:
    if len(response.content) > MAX_REPLY_BYTES:
        raise LLMOutputInvalid("image embedding reply too large")
    try:
        body: Any = response.json()
        items = cast("list[dict[str, Any]]", cast("dict[str, Any]", body)["data"])
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
        vectors = [[float(x) for x in item["embedding"]] for item in ordered]
    except (ValueError, KeyError, TypeError):
        raise LLMOutputInvalid("image embedding reply has no vectors") from None
    if len(vectors) != count or len({len(v) for v in vectors}) != 1:
        raise LLMOutputInvalid(f"{len(vectors)} image vectors for {count} images")
    return [_normalised(v) for v in vectors]


async def post_image_embeddings(
    deployment: Deployment, images: list[str], *, settings: LlmSettings
) -> list[list[float]]:
    proxy = settings.egress_proxy_url if not deployment.local else None
    headers = {"Authorization": f"Bearer {deployment.api_key}"} if deployment.api_key else {}
    url = f"{deployment.base_url.rstrip('/')}/embeddings"
    timeout = deployment.timeout_seconds or settings.timeout_seconds
    async with httpx.AsyncClient(
        proxy=proxy, trust_env=False, follow_redirects=False, timeout=timeout
    ) as client:
        try:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": deployment.model,
                    "input": images,
                    "modality": "image",
                    "encoding_format": "float",
                },
            )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"{deployment.name}: {type(exc).__name__}") from exc
    status = response.status_code
    if status >= 500 or status in (408, 429):
        raise ModelUnavailable(f"{deployment.name}: HTTP {status}")
    if status != 200:
        raise ModelRejected(f"{deployment.name}: HTTP {status}")
    return parse_vectors(response, len(images))
