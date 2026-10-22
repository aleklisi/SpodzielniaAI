"""Which features have the models they need (10.2, invariant P5).

A feature is available when every purpose it needs has a usable deployment: routed, allowed by
the cloud ceiling and, for private purposes, cleared for private data. The frontend hides or
disables the rest with „Model nie jest skonfigurowany”, and the admin console lists the missing
purposes. Nothing here calls a model; availability is about configuration, not health.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.core.metrics import LLM_PURPOSE_CONFIGURED
from spoldzielnia_ai.llm.gateway import LLMPurpose

if TYPE_CHECKING:
    from spoldzielnia_ai.llm.gateway import LiteLLMGateway

# Feature -> the purposes it needs. The keys are the API's (and the frontend's) feature names.
FEATURES: dict[str, tuple[LLMPurpose, ...]] = {
    "wiki_answers": (LLMPurpose.RAG, LLMPurpose.EMBED),
    "notebook_answers": (LLMPurpose.PRIVATE_NOTE, LLMPurpose.EMBED),
    "notebook_search": (LLMPurpose.EMBED,),
    "promotion": (LLMPurpose.PROMOTION,),
    "image_descriptions": (LLMPurpose.VLM,),
    "recognition": (LLMPurpose.VLM,),
}

ROUTED_PURPOSES = tuple(p for p in LLMPurpose if p != LLMPurpose.PLAYGROUND)


async def missing_purposes(gateway: LiteLLMGateway) -> list[LLMPurpose]:
    """The purposes without a usable model (refreshes the configuration first)."""
    await gateway.refresh()
    missing = [p for p in ROUTED_PURPOSES if not gateway.configured(p)]
    for purpose in ROUTED_PURPOSES:
        LLM_PURPOSE_CONFIGURED.labels(purpose=purpose.value).set(0 if purpose in missing else 1)
    return missing


async def capabilities(gateway: LiteLLMGateway) -> dict[str, bool]:
    missing = set(await missing_purposes(gateway))
    return {name: not (set(needs) & missing) for name, needs in FEATURES.items()}
