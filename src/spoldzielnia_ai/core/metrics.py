"""Prometheus metrics (4.4). Served by the API at ``/metrics`` on the internal network only:
the proxy answers 404 for ``/api/metrics``."""

from __future__ import annotations

from prometheus_client import Gauge, Histogram

CONSENT_PROPAGATION = Histogram(
    "consent_propagation_seconds",
    "From a consent request's start until its Qdrant operations are applied",
    ["action"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
OUTBOX_BACKLOG = Gauge("vector_outbox_backlog", "Canonical-index operations waiting in the outbox")
