"""Questions answered from knowledge: the member's notebook (5.1) and the cooperative (5.6).

Answers are syntheses with citations only (AGENTS.md §3.3). Notebook Q&A runs in the member's
RLS session on the local model. Cooperative questions go through the
``AntiDistillationGuard`` and nothing else: this module never imports ``RAGService``
(import-linter contract in pyproject.toml).

Shield responses: ``403 security_lock`` (account locked), ``429`` + ``Retry-After``
(24h coverage throttle), ``401 {challenge, pow}`` (step-up: re-send with ``X-Shield-PoW:
nonce:counter`` or after a fresh MFA login).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status

from spoldzielnia_ai.api.capabilities import model_not_configured
from spoldzielnia_ai.auth.deps import CurrentUser, step_up_url
from spoldzielnia_ai.llm.gateway import LLMError, ModelNotConfigured
from spoldzielnia_ai.notebook.deps import NotebookDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.rag.deps import NotebookQADep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.rag.schemas import AskIn, NoteAnswer, RagAnswer
from spoldzielnia_ai.shield.challenge import HEADER as POW_HEADER
from spoldzielnia_ai.shield.guard import Asker
from spoldzielnia_ai.shield.wiring import GuardDep  # noqa: TC001 - FastAPI resolves it

router = APIRouter(tags=["rag"])


def check_question(request: Request, payload: AskIn) -> str:
    question = payload.question.strip()
    limit = request.app.state.settings.rag.question_max_chars
    if len(question) < 3 or len(question) > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "question_length", "max_chars": limit},
        )
    return question


def model_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "model_unavailable"},
        headers={"Retry-After": "30"},
    )


@router.post("/notes/ask", response_model=NoteAnswer)
async def ask_notes(
    payload: AskIn,
    request: Request,
    user: CurrentUser,
    notebook: NotebookDep,
    qa: NotebookQADep,
) -> NoteAnswer:
    """Ask the member's own notebook."""
    question = check_question(request, payload)
    try:
        return await qa.ask(notebook, user.id, question)
    except ModelNotConfigured:
        raise model_not_configured() from None
    except LLMError:
        raise model_unavailable() from None


@router.post("/rag/ask", response_model=RagAnswer)
async def ask_cooperative(
    payload: AskIn,
    request: Request,
    principal: CurrentUser,
    guard: GuardDep,
    pow_solution: Annotated[str | None, Header(alias=POW_HEADER)] = None,
) -> RagAnswer:
    """Ask the cooperative's canonical knowledge."""
    question = check_question(request, payload)
    asker = Asker(
        principal.id,
        principal.mfa_satisfied,
        principal.session.auth_time,
        step_up_url(request.app.state.settings),
    )
    try:
        return await guard.ask(asker, question, pow_solution)
    except ModelNotConfigured:
        raise model_not_configured() from None
    except LLMError:
        raise model_unavailable() from None
