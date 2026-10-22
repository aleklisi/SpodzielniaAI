"""Which endpoints an admin may configure, whether they are local (7.1, invariants S3/S4), and
which may receive private data (10.1, invariant P2).

An admin-entered base URL is never trusted on its own:

- **LOCAL** only if its origin (scheme, host, port) is ``llm.local_base_url``'s or one of
  ``llm.local_endpoints``. Locality is derived from that match, never from a checkbox, so a
  cloud host cannot be declared local to receive private data.
- **CLOUD** only over ``https`` to a host on ``llm.cloud_hosts`` (or ``llm.cloud_base_url``'s
  host), on the default port. Cloud traffic goes out through the egress proxy (7.3).
- Anything else is refused: the backend network must not become reachable through a URL typed
  into the admin console (SSRF). No user info, query or fragment; no DNS lookups here, because
  allowlists are exact names.

The same classification runs again when a process loads the active configuration, so a
tampered database row cannot turn a cloud endpoint into a local one either.

Locality only decides the network path (direct, or through the egress proxy). Whether private
data may go to an endpoint is ``may_receive_private_data``: its origin must be on
``llm.private_data_endpoints`` (default: the local endpoints), and an admin must have cleared
the provider. The env list is the ceiling; a cleared flag outside it counts for nothing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from spoldzielnia_ai.db.enums import LlmProviderKind

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import LlmSettings

MAX_URL_LENGTH = 512
_DEFAULT_PORTS = {"http": 80, "https": 443}
_UNSAFE = re.compile(r"[\s\x00-\x1f\x7f\\]")


class EndpointNotAllowed(ValueError):
    """``reason`` is a short code for API errors (``invalid_url``, ``endpoint_not_allowed`` …)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _origin(url: str) -> tuple[str, str, int]:
    if len(url) > MAX_URL_LENGTH or _UNSAFE.search(url):
        raise EndpointNotAllowed("invalid_url")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise EndpointNotAllowed("invalid_url") from None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if scheme not in _DEFAULT_PORTS or not host:
        raise EndpointNotAllowed("invalid_url")
    if parts.username is not None or parts.password is not None:
        raise EndpointNotAllowed("credentials_in_url")
    if parts.query or parts.fragment:
        raise EndpointNotAllowed("invalid_url")
    return scheme, host, port or _DEFAULT_PORTS[scheme]


def normalize_base_url(url: str) -> str:
    """``url`` without surrounding whitespace and trailing slashes (validated)."""
    cleaned = url.strip().rstrip("/")
    _origin(cleaned)
    return cleaned


def _local_origins(settings: LlmSettings) -> set[tuple[str, str, int]]:
    origins: set[tuple[str, str, int]] = set()
    for url in (settings.local_base_url, *settings.local_endpoints):
        try:
            origins.add(_origin(url.strip()))
        except EndpointNotAllowed:
            continue  # a broken allowlist entry allows nothing
    return origins


def _cloud_hosts(settings: LlmSettings) -> set[str]:
    hosts = {h.strip().lower().rstrip(".") for h in settings.cloud_hosts if h.strip()}
    if settings.cloud_base_url:
        try:
            scheme, host, _ = _origin(settings.cloud_base_url)
        except EndpointNotAllowed:
            return hosts
        if scheme == "https":
            hosts.add(host)
    return hosts


def _private_origins(settings: LlmSettings) -> set[tuple[str, str, int]]:
    if settings.private_data_endpoints is None:
        return _local_origins(settings)
    origins: set[tuple[str, str, int]] = set()
    for url in settings.private_data_endpoints:
        try:
            origins.add(_origin(url.strip()))
        except EndpointNotAllowed:
            continue
    return origins


def may_receive_private_data(url: str, settings: LlmSettings) -> bool:
    """True if the operator allows clearing ``url``'s origin for private data (P2)."""
    try:
        return _origin(url.strip()) in _private_origins(settings)
    except EndpointNotAllowed:
        return False


def _display(origin: tuple[str, str, int]) -> str:
    scheme, host, port = origin
    return f"{scheme}://{host}" + ("" if _DEFAULT_PORTS[scheme] == port else f":{port}")


def private_data_endpoints(settings: LlmSettings) -> list[str]:
    """For the admin UI: the origins that may be cleared for private data."""
    return sorted(_display(o) for o in _private_origins(settings))


def classify(url: str, settings: LlmSettings) -> LlmProviderKind:
    """LOCAL or CLOUD for an allowed endpoint; ``EndpointNotAllowed`` otherwise."""
    origin = _origin(url.strip())
    if origin in _local_origins(settings):
        return LlmProviderKind.LOCAL
    scheme, host, port = origin
    if host in _cloud_hosts(settings):
        if scheme != "https" or port != _DEFAULT_PORTS["https"]:
            raise EndpointNotAllowed("cloud_requires_https")
        return LlmProviderKind.CLOUD
    raise EndpointNotAllowed("endpoint_not_allowed")


def allowed_endpoints(settings: LlmSettings) -> tuple[list[str], list[str]]:
    """For the admin UI: the local origins and the cloud hosts admins may use."""
    local = sorted(_display(o) for o in _local_origins(settings))
    return local, sorted(_cloud_hosts(settings))
