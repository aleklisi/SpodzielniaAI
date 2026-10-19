"""The output side of the shield (5.5): verify, then refuse or watermark.

A draft that copies its sources is replaced by a refusal that still cites the articles (the
member can read them in the Wiki, where reading counts against coverage), audited as
``VERBATIM_BLOCKED`` with the measurements only, and adds ``verbatim_penalty`` to the
account's risk. A draft that passes is watermarked for the account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.rag.synthesis import markers
from spoldzielnia_ai.shield.output_verifier import REFUSAL, OutputVerifier, Verification

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.rag.synthesis import Source
    from spoldzielnia_ai.shield.risk import RiskStore
    from spoldzielnia_ai.shield.watermark import Watermarker

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Checked:
    text: str
    blocked: bool
    verification: Verification


class OutputShield:
    def __init__(
        self,
        factory: SessionFactory,
        risk: RiskStore,
        settings: ShieldSettings,
        watermarker: Watermarker | None,
    ) -> None:
        self._factory = factory
        self._risk = risk
        self._settings = settings
        self._verifier = OutputVerifier(settings)
        self._watermarker = watermarker

    async def check(
        self, user_id: UUID, request_id: UUID, draft: str, sources: Sequence[Source]
    ) -> Checked:
        verification = self._verifier.verify(draft, [s.text for s in sources])
        if not verification.blocked:
            text = self._watermarker.apply(draft, user_id) if self._watermarker else draft
            return Checked(text, False, verification)
        risk = await self._risk.add(user_id, self._settings.verbatim_penalty)
        async with self._factory() as session, session.begin():
            await AuditService(session).record(
                EventType.VERBATIM_BLOCKED,
                user_id,
                AuditTarget("rag_request", str(request_id)),
                {**verification.to_json(), "risk": round(risk, 4)},
            )
        log.warning("shield.verbatim_blocked", user_id=str(user_id), reason=verification.reason)
        labels = {s.label for s in sources}
        cited = [m for m in dict.fromkeys(markers(draft)) if m in labels]
        return Checked(REFUSAL + " " + "".join(f"[{m}]" for m in cited), True, verification)
