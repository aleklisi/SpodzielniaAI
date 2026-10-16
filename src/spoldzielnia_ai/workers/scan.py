"""Prompt-injection screening of finished derivatives (``core.scan``, worker-core, chunk 3.3).

Reads and decrypts in one short RLS transaction, scans outside any transaction (the classifier
can take seconds on long documents), then writes the verdict. Above the threshold the
derivative becomes QUARANTINED (excluded from promotion until its owner confirms it) and
``PROMPT_INJECTION_DETECTED`` is audited, with metadata only.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import DerivativeStatus
from spoldzielnia_ai.db.models import NoteDerivative
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.llm.injection_guard import document_flags
from spoldzielnia_ai.notebook.service import DERIVATIVE

if TYPE_CHECKING:
    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.llm.injection_guard import InjectionGuard
    from spoldzielnia_ai.workers.jobs import DerivativeJob

log = get_logger(__name__)


class DerivativeScanner:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        guard: InjectionGuard,
        classifier_available: bool,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._guard = guard
        self._classifier_available = classifier_available

    async def handle(self, job: DerivativeJob, final_attempt: bool) -> bool:
        async with rls_session(self._factory, job.owner_id) as session:
            row = await session.get(NoteDerivative, job.derivative_id)
            if row is None or row.status != DerivativeStatus.DONE or row.content_ciphertext is None:
                return False
            if "scan" in row.injection_flags:
                return False  # redelivered
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
            content = self._cipher.decrypt_field(dek, row.content_ciphertext, aad, "content")
            stored: dict[str, Any] = dict(row.injection_flags)
            kind = row.kind

        verdict = await asyncio.to_thread(
            self._guard.scan, content.decode(), document_flags(stored)
        )

        async with rls_session(self._factory, job.owner_id) as session:
            row = await session.get(NoteDerivative, job.derivative_id, with_for_update=True)
            if row is None or row.status != DerivativeStatus.DONE:
                return False
            flags = verdict.to_flags()
            flags["classifier"] = "ok" if self._classifier_available else "unavailable"
            row.injection_flags = {**stored, "scan": flags}
            if verdict.quarantine:
                row.status = DerivativeStatus.QUARANTINED
                payload: dict[str, JsonValue] = {
                    "kind": kind.value,
                    "score": verdict.score,
                    "flag_kinds": list[JsonValue](verdict.kinds()),
                    "rules": list[JsonValue](sorted({f.rule for f in verdict.flags if f.rule})),
                }
                await AuditService(session).record(
                    EventType.PROMPT_INJECTION_DETECTED,
                    job.owner_id,
                    AuditTarget(DERIVATIVE, str(job.derivative_id)),
                    payload,
                )
        log.info(
            "worker.scanned",
            derivative=str(job.derivative_id),
            score=flags["score"],
            quarantined=verdict.quarantine,
        )
        return False
