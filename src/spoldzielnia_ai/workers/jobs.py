"""What crosses the queue: identifiers only, never content (chunk 2.5).

``DerivativeJob`` is strict. Unknown keys, non-UUID values or anything that looks like a payload
are refused at runtime, and the typed ``enqueue`` signature refuses them at type-check time.
Workers reload everything through ``rls_session(owner_id)``.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID  # noqa: TC003 - pydantic resolves the annotation at runtime

from pydantic import BaseModel, ConfigDict

from spoldzielnia_ai.db.enums import DerivativeKind


class Queue(StrEnum):
    TRANSCRIBE = "media.transcribe"
    VISION = "media.vision"
    PARSE = "media.parse"
    FETCH = "egress.fetch"
    CORE_SCAN = "core.scan"  # prompt-injection screening of derivatives (3.3)
    PROMOTION = "promotion"  # push-to-wiki (3.5)
    INDEX = "index"  # Stage 4


class TaskName(StrEnum):
    TRANSCRIBE_AUDIO = "media.transcribe_audio"
    DESCRIBE_IMAGE = "media.describe_image"
    PARSE_DOCUMENT = "media.parse_document"
    FETCH_LINK = "egress.fetch_link"


TASK_QUEUE: dict[TaskName, Queue] = {
    TaskName.TRANSCRIBE_AUDIO: Queue.TRANSCRIBE,
    TaskName.DESCRIBE_IMAGE: Queue.VISION,
    TaskName.PARSE_DOCUMENT: Queue.PARSE,
    TaskName.FETCH_LINK: Queue.FETCH,
}

KIND_TASK: dict[DerivativeKind, TaskName] = {
    DerivativeKind.TRANSCRIPT: TaskName.TRANSCRIBE_AUDIO,
    DerivativeKind.IMAGE_DESCRIPTION: TaskName.DESCRIBE_IMAGE,
    DerivativeKind.PARSED_DOC: TaskName.PARSE_DOCUMENT,
    DerivativeKind.FETCHED_LINK: TaskName.FETCH_LINK,
}

# Per-task limits (seconds): soft → the task marks the derivative FAILED, hard → killed.
TIME_LIMITS: dict[TaskName, tuple[int, int]] = {
    TaskName.TRANSCRIBE_AUDIO: (3600, 3660),
    TaskName.DESCRIBE_IMAGE: (300, 330),
    TaskName.PARSE_DOCUMENT: (900, 960),
    TaskName.FETCH_LINK: (60, 90),
}


class DerivativeJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)

    derivative_id: UUID
    owner_id: UUID

    def to_message(self) -> dict[str, str]:
        return {"derivative_id": str(self.derivative_id), "owner_id": str(self.owner_id)}
