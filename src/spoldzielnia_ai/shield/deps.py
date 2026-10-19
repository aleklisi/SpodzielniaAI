"""Request dependencies and HTTP mapping for the shield.

Refusals don't say which detector fired (SECURITY.md layer 5: never reveal detection): an
account lock is ``403 security_lock``, a throttle ``429`` with ``Retry-After``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status

from spoldzielnia_ai.shield.coverage import CorpusCounter, CoverageTracker
from spoldzielnia_ai.shield.locks import AccountLocker

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.shield.coverage import CoverageExceeded


def get_locker(request: Request) -> AccountLocker:
    return AccountLocker(request.app.state.session_factory, request.app.state.notifier)


def get_corpus(request: Request) -> CorpusCounter:
    state = request.app.state
    settings: Settings = state.settings
    return CorpusCounter(
        state.redis, state.session_factory, settings.shield.corpus_size_ttl_seconds
    )


def get_coverage(request: Request) -> CoverageTracker:
    settings: Settings = request.app.state.settings
    return CoverageTracker(
        request.app.state.redis, get_corpus(request), settings.shield, get_locker(request)
    )


CoverageDep = Annotated[CoverageTracker, Depends(get_coverage)]


def security_lock() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "security_lock"})


def throttled(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"error": "rate_limited"},
        headers={"Retry-After": str(max(retry_after, 1))},
    )


def coverage_refusal(exc: CoverageExceeded) -> HTTPException:
    if exc.account_locked or exc.retry_after is None:
        return security_lock()
    return throttled(exc.retry_after)
