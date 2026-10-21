"""Prometheus metrics (4.4). Served by the API at ``/metrics`` on the internal network only:
the proxy answers 404 for ``/api/metrics``."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

CONSENT_PROPAGATION = Histogram(
    "consent_propagation_seconds",
    "From a consent request's start until its Qdrant operations are applied",
    ["action"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
OUTBOX_BACKLOG = Gauge("vector_outbox_backlog", "Canonical-index operations waiting in the outbox")

# LLM gateway (7.2). Deployment names come from the admin's configuration (bounded).
LLM_REQUEST_SECONDS = Histogram(
    "llm_request_seconds",
    "Model call latency per deployment and purpose",
    ["deployment", "purpose"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)
LLM_ERRORS = Counter(
    "llm_request_errors_total",
    "Failed model calls (unavailable, rejected, invalid_output)",
    ["deployment", "kind"],
)
LLM_ROUTE_REFUSED = Counter(
    "llm_route_refused_total",
    "Local-only calls whose configured route was not local (refused; should stay 0)",
    ["purpose"],
)
LLM_CONFIG_REVISION = Gauge(
    "llm_config_revision", "Active LLM configuration revision in this process (0 = .env)"
)

USER_ADMIN_ACTIONS = Counter(
    "user_admin_actions_total",
    "Admin user-management actions by outcome (ok or the refusal code)",
    ["action", "outcome"],
)
KEYCLOAK_ADMIN_SECONDS = Histogram(
    "keycloak_admin_seconds",
    "Keycloak Admin REST API call latency",
    ["method"],
)
