"""``/capabilities``: which features have the models they need right now (10.2, P5).

For every signed-in member: the frontend hides or disables a feature that is off and says
„Model nie jest skonfigurowany”. It names features, not models or providers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.llm.capabilities import FEATURES, capabilities

if TYPE_CHECKING:
    from spoldzielnia_ai.llm.gateway import LiteLLMGateway, LLMPurpose

router = APIRouter(tags=["capabilities"])


class CapabilitiesOut(BaseModel):
    features: dict[str, bool]


@router.get("/capabilities", response_model=CapabilitiesOut)
async def get_capabilities(request: Request, _user: CurrentUser) -> CapabilitiesOut:
    gateway: LiteLLMGateway = request.app.state.llm
    return CapabilitiesOut(features=await capabilities(gateway))


def model_not_configured() -> HTTPException:
    """503 without Retry-After: retrying won't help until an admin configures a model."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "model_not_configured"}
    )


async def require_feature(request: Request, feature: str) -> None:
    """Refuse a request up front when ``feature`` has no model (no job is created)."""
    gateway: LiteLLMGateway = request.app.state.llm
    await gateway.refresh()
    needs: tuple[LLMPurpose, ...] = FEATURES[feature]
    if not all(gateway.configured(p) for p in needs):
        raise model_not_configured()
