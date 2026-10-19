"""Admin alerts from the shield (coverage lock, honeypot hit, account lock).

The audit log is the record; an alert is the nudge. ``LogNotifier`` writes an error-level
structured log (collected by the log pipeline). ``WebhookNotifier`` also POSTs the alert as
JSON to an internal relay (the API container has no internet). Alerts carry ids, reason codes
and numbers only, and a failing webhook never fails the request that raised the alert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import httpx

from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.core.settings import ShieldSettings

log = get_logger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class Alert:
    kind: str  # e.g. "COVERAGE_LOCK", "HONEYPOT_TRIGGERED"
    user_id: UUID
    detail: dict[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])

    def to_json(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, "user_id": str(self.user_id), "detail": self.detail}


class Notifier(Protocol):
    async def notify(self, alert: Alert) -> None: ...


class LogNotifier:
    async def notify(self, alert: Alert) -> None:
        log.error("shield.alert", kind=alert.kind, user_id=str(alert.user_id), **alert.detail)


class WebhookNotifier(LogNotifier):
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = url
        self._transport = transport

    async def notify(self, alert: Alert) -> None:
        await super().notify(alert)
        try:
            async with httpx.AsyncClient(
                timeout=WEBHOOK_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                response = await client.post(self._url, json=alert.to_json())
                response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("shield.alert_webhook_failed", kind=alert.kind, error=type(exc).__name__)


def make_notifier(settings: ShieldSettings) -> Notifier:
    if settings.alert_webhook_url:
        return WebhookNotifier(settings.alert_webhook_url)
    return LogNotifier()
