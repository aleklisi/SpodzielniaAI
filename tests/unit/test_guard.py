"""Chunk 5.6: RiskEngine levels, proof-of-work step-up, and "nothing but the guard reaches
``RAGService``"."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fakeredis import FakeAsyncRedis

from spoldzielnia_ai.core.settings import ShieldSettings
from spoldzielnia_ai.shield.challenge import (
    PowChallenges,
    is_solution,
    leading_zero_bits,
    solve,
)
from spoldzielnia_ai.shield.coverage import CoverageLevel, CoverageStatus, Window, WindowUsage
from spoldzielnia_ai.shield.risk import RiskEngine, RiskLevel, RiskStore
from spoldzielnia_ai.shield.semantic import Features, SemanticVerdict

SRC = Path(__file__).resolve().parents[2] / "src" / "spoldzielnia_ai"


def _coverage(fraction_of_7d_limit: float, blocks: int = 1000) -> CoverageStatus:
    ratio = 0.15 * fraction_of_7d_limit
    usage = {
        Window.H24: WindowUsage(0, 0, 0.0, 0.05),
        Window.D7: WindowUsage(blocks, 2, ratio, 0.15),
        Window.D30: WindowUsage(blocks, 2, ratio, 0.25),
    }
    return CoverageStatus(usage, CoverageLevel.OK)


def _semantic(score: float, hard: bool = False) -> SemanticVerdict:
    return SemanticVerdict(score, Features(queries=10), anomalous=score >= 0.7, hard=hard)


@pytest.mark.parametrize(
    ("incidents", "coverage", "semantic", "level"),
    [
        (0.0, 0.0, 0.0, RiskLevel.NORMAL),
        (0.0, 0.6, 0.1, RiskLevel.NORMAL),  # 0.144 + 0.06
        (0.0, 0.9, 0.0, RiskLevel.ELEVATED),  # 0.324
        (0.4, 0.0, 0.0, RiskLevel.ELEVATED),
        (0.0, 0.0, 0.9, RiskLevel.HIGH),  # 0.54
        (0.6, 0.0, 0.0, RiskLevel.HIGH),
        (0.5, 1.0, 0.0, RiskLevel.CRITICAL),  # 0.9
        (0.0, 1.0, 0.9, RiskLevel.CRITICAL),
    ],
)
async def test_risk_levels(
    incidents: float, coverage: float, semantic: float, level: RiskLevel
) -> None:
    settings = ShieldSettings()
    store = RiskStore(FakeAsyncRedis(), settings)
    user = uuid4()
    if incidents:
        await store.add(user, incidents)

    assessment = await RiskEngine(store, settings).assess(
        user, _coverage(coverage), _semantic(semantic)
    )

    assert assessment.level == level
    assert 0.0 <= assessment.score <= 1.0


async def test_coverage_pressure_respects_the_small_corpus_floor() -> None:
    """Gap G14 applies to escalation too: over the ratio but far below the absolute floor."""
    settings = ShieldSettings(min_blocks_before_lock=50)
    engine = RiskEngine(RiskStore(FakeAsyncRedis(), settings), settings)
    small = await engine.assess(uuid4(), _coverage(2.0, blocks=10), _semantic(0))
    large = await engine.assess(uuid4(), _coverage(2.0, blocks=60), _semantic(0))
    assert small.coverage == pytest.approx(0.4 * (10 / 50) ** 2)
    assert small.level == RiskLevel.NORMAL
    assert large.coverage == pytest.approx(0.4)


async def test_hard_semantic_score_is_critical_on_its_own() -> None:
    settings = ShieldSettings()
    engine = RiskEngine(RiskStore(FakeAsyncRedis(), settings), settings)
    assessment = await engine.assess(uuid4(), _coverage(0), _semantic(0.98, hard=True))
    assert assessment.level == RiskLevel.CRITICAL


def test_risk_levels_must_ascend() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ShieldSettings(risk_elevated=0.6, risk_high=0.5)


# --- Proof of work ------------------------------------------------------------------------------


def test_leading_zero_bits() -> None:
    assert leading_zero_bits(b"\x00\x00\x80") == 16
    assert leading_zero_bits(b"\x00\x0f") == 12
    assert leading_zero_bits(b"\xff") == 0


async def test_a_solution_lets_exactly_one_request_through() -> None:
    challenges = PowChallenges(FakeAsyncRedis(), difficulty=10, ttl_seconds=60)
    user = uuid4()
    puzzle = await challenges.issue(user)
    counter = solve(puzzle.nonce, puzzle.difficulty)
    assert is_solution(puzzle.nonce, counter, 10)

    assert await challenges.redeem(uuid4(), f"{puzzle.nonce}:{counter}") is False  # not theirs
    assert await challenges.redeem(user, f"{puzzle.nonce}:{counter}") is True
    assert await challenges.redeem(user, f"{puzzle.nonce}:{counter}") is False  # single use


async def test_wrong_or_malformed_solutions_are_refused() -> None:
    challenges = PowChallenges(FakeAsyncRedis(), difficulty=12, ttl_seconds=60)
    user = uuid4()
    puzzle = await challenges.issue(user)
    wrong = next(str(c) for c in range(10_000) if not is_solution(puzzle.nonce, str(c), 12))
    for solution in (None, "", "no-colon", "x" * 200, f"{puzzle.nonce}:{wrong}"):
        assert await challenges.redeem(user, solution) is False
    # A wrong attempt burned the nonce: brute-forcing online is not an option.
    assert await challenges.redeem(user, f"{puzzle.nonce}:{solve(puzzle.nonce, 12)}") is False


# --- Guard bypass -------------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_only_the_guard_and_its_wiring_import_rag_service() -> None:
    importers = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "spoldzielnia_ai.rag.service" in _imports(path)
    }
    assert importers == {"shield/guard.py", "shield/wiring.py"}


def test_only_the_guard_calls_rag_ask() -> None:
    callers: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "ask"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "rag"
            ):
                callers.add(str(path.relative_to(SRC)))
    assert callers == {"shield/guard.py"}


def test_import_linter_contract_holds() -> None:
    from importlinter.cli import lint_imports

    assert lint_imports(config_filename=str(SRC.parents[1] / "pyproject.toml")) == 0


async def test_difficulty_escalates_with_each_solved_puzzle_and_open_ones_are_counted() -> None:
    challenges = PowChallenges(
        FakeAsyncRedis(), difficulty=4, ttl_seconds=60, escalation_bits=2, max_bits=9
    )
    user = uuid4()
    difficulties: list[int] = []
    for _ in range(4):
        puzzle = await challenges.issue(user)
        difficulties.append(puzzle.difficulty)
        assert await challenges.open(user) == 1
        solution = f"{puzzle.nonce}:{solve(puzzle.nonce, puzzle.difficulty)}"
        assert await challenges.redeem(user, solution)
        assert await challenges.open(user) == 0
    assert difficulties == [4, 6, 8, 9]  # capped at max_bits

    for _ in range(3):
        await challenges.issue(user)
    assert await challenges.open(user) == 3
