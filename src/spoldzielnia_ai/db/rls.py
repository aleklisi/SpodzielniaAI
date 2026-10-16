"""Sessions bound to one user for Row-Level Security on private-zone tables (AGENTS.md §3.1).

``rls_session`` sets ``app.current_user_id`` with ``set_config(..., is_local => true)``, the
transaction-scoped equivalent of ``SET LOCAL``. The setting ends with the transaction, so a pooled
connection never carries one user's id into the next request. The context manager owns the
transaction: callers must not ``commit()`` inside it. If they do, the next transaction has no
setting and private tables come back empty (fail closed).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, NewType
from uuid import UUID  # noqa: TC003 - FastAPI resolves dependency annotations at runtime

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spoldzielnia_ai.auth.deps import current_user_id

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from spoldzielnia_ai.db.session import SessionFactory

RlsSession = NewType("RlsSession", AsyncSession)
"""An ``AsyncSession`` in a transaction with ``app.current_user_id`` set.

Only ``rls_session`` creates one. Repositories for private-zone tables accept nothing else.
"""

_SET_CURRENT_USER = text("SELECT set_config('app.current_user_id', :uid, true)")


@asynccontextmanager
async def rls_session(factory: SessionFactory, user_id: UUID) -> AsyncGenerator[RlsSession]:
    """A session in one transaction acting as ``user_id``. Commits on exit, rolls back on error."""
    async with factory() as session, session.begin():
        await session.execute(_SET_CURRENT_USER, {"uid": str(user_id)})
        yield RlsSession(session)


async def get_rls_session(
    request: Request, user_id: Annotated[UUID, Depends(current_user_id)]
) -> AsyncIterator[RlsSession]:
    """FastAPI dependency: an RLS session for the authenticated user."""
    factory: SessionFactory = request.app.state.session_factory
    async with rls_session(factory, user_id) as session:
        yield session


# `scope="function"` ends the transaction before the response is sent, so a failed commit
# turns into an error response instead of a 2xx for data that was never stored.
RlsSessionDep = Annotated[RlsSession, Depends(get_rls_session, scope="function")]
