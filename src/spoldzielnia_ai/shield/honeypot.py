"""Honeypot (canary) blocks (5.4, SECURITY.md layer 4).

**Canaries.** Plausible but fictitious internal procedures ("Procedura wewnętrzna
PSZ-7QK2M4XA/2026 dotycząca …"), generated from templates. Each carries a signature token
``{prefix}-{nonce}{mac}/{year}``: ``mac`` is an HMAC (``honeypot_key``) over the nonce, so a
token found anywhere can be verified without a database (``find_signatures``).

**Placement.** The text mixes three unrelated topics, and of several candidates the one whose
embedding sits most *between* two topic clusters (5.3) is kept: narrow member questions land
near one cluster and rarely reach it, broad sweeps do.

**Storage.** Blocks with ``is_honeypot=true``, authored by a system account, in one hidden
CANONICAL article (``is_hidden``): indexed and retrievable like real knowledge (Qdrant payload
``is_honeypot=true``), but never listed, served, reviewed, counted in coverage or clustered.

**Retrieval hook** (``HoneypotMonitor``, called by the guard): a hit is removed before
synthesis (gap G15, enforced in ``RAGService`` as well), adds ``honeypot_penalty`` to the
account's risk, is audited (``HONEYPOT_TRIGGERED``: honeypot ids and a hash of the query) and
alerts an admin. ``honeypot_lock_after`` hits within 30 days lock the account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus
from spoldzielnia_ai.db.models import ArticleBlock, User, WikiArticle
from spoldzielnia_ai.shield.alerts import Alert
from spoldzielnia_ai.shield.clusters import load_clusters, normalize
from spoldzielnia_ai.vector.sync import reindex_articles

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from pydantic import JsonValue
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.core.settings import ShieldSettings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.shield.alerts import Notifier
    from spoldzielnia_ai.shield.locks import Locker
    from spoldzielnia_ai.shield.risk import RiskStore
    from spoldzielnia_ai.vector.embedding import Embedder
    from spoldzielnia_ai.vector.revalidate import LiveBlock
    from spoldzielnia_ai.vector.sync import VectorSync

log = get_logger(__name__)

SYSTEM_ISSUER = "urn:spoldzielnia:system"
SYSTEM_SUBJECT = "honeypot"
HIDDEN_TITLE = "Archiwum procedur wewnętrznych"
HITS_TTL_SECONDS = 30 * 86400
MAX_PER_INJECTION = 20
CANDIDATES = 5

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
SIGNATURE = re.compile(r"\b([A-Z]{2,5})-([A-Z2-7]{4})([A-Z2-7]{4})/(\d{4})\b")

TOPICS = (
    "leczenia warrozy",
    "zimowli rodzin pszczelich",
    "wymiany matek pszczelich",
    "dokarmiania jesiennego",
    "pozyskiwania pyłku kwiatowego",
    "rejestracji pasiek w inspektoracie weterynarii",
    "rozliczania sprzedaży bezpośredniej miodu",
    "transportu uli na pożytki",
    "kontroli zgnilca amerykańskiego",
    "pozyskiwania propolisu",
    "ubezpieczenia pasiek",
    "znakowania słoików z miodem",
    "odkładów i podziału rodzin",
    "dezynfekcji sprzętu pasiecznego",
)
MEASURES = (
    "dwuetapową kartę kontroli ula",
    "protokół obserwacji osypu",
    "rejestr zabiegów weterynaryjnych",
    "tygodniowy arkusz przeglądu",
    "procedurę zgłoszeń do koordynatora rejonu",
    "kwartalne sprawozdanie pasieczne",
)
MONTHS = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)
TEMPLATE = (
    "Procedura wewnętrzna {signature} dotycząca {a}, {b} oraz {c}. Zgodnie z ustaleniami Rady "
    "Ekspertów z {month} {year} r. pasieki członkowskie prowadzą {measure} i przekazują wyniki "
    "do rejestru {register} w terminie {days} dni od zakończenia sezonu."
)


class HoneypotKeyMissing(RuntimeError):
    """``shield.honeypot_key`` is not configured (Docker secret ``honeypot_key``)."""


def signing_key(settings: ShieldSettings) -> bytes:
    if settings.honeypot_key is None or not settings.honeypot_key.get_secret_value().strip():
        raise HoneypotKeyMissing("shield.honeypot_key is not set")
    return settings.honeypot_key.get_secret_value().strip().encode()


def _mac(key: bytes, prefix: str, nonce: str, year: str) -> str:
    digest = hmac.new(key, f"{prefix}:{nonce}:{year}".encode(), hashlib.sha256).digest()
    return base64.b32encode(digest).decode()[:4]


def make_signature(key: bytes, prefix: str, year: int) -> str:
    nonce = "".join(secrets.choice(_B32) for _ in range(4))
    return f"{prefix}-{nonce}{_mac(key, prefix, nonce, str(year))}/{year}"


def find_signatures(text: str, key: bytes) -> list[str]:
    """Valid canary tokens in ``text`` (case-insensitive, in order, without repeats)."""
    found: list[str] = []
    for m in SIGNATURE.finditer(text.upper()):
        prefix, nonce, mac, year = m.groups()
        if hmac.compare_digest(_mac(key, prefix, nonce, year), mac) and m.group(0) not in found:
            found.append(m.group(0))
    return found


def canary_text(signature: str, now: datetime) -> str:
    rng = secrets.SystemRandom()
    a, b, c = rng.sample(TOPICS, 3)
    return TEMPLATE.format(
        signature=signature,
        a=a,
        b=b,
        c=c,
        month=rng.choice(MONTHS),
        year=now.year - rng.randint(0, 2),
        measure=rng.choice(MEASURES),
        register=f"{rng.choice('RKA')}{rng.choice('PZR')}-{rng.randint(10, 99)}",
        days=rng.choice((7, 14, 21, 30)),
    )


def betweenness(vector: np.ndarray, centroids: np.ndarray) -> float:
    """1.0 when equally similar to its two closest clusters, near 0 inside one cluster."""
    sims = np.sort(centroids @ normalize(vector[None, :])[0])[::-1]
    if len(sims) < 2 or sims[0] <= 0:
        return 0.0
    return float(max(sims[1], 0.0) / sims[0])


@dataclass(frozen=True, slots=True)
class Canary:
    block_id: UUID
    signature: str


@dataclass(frozen=True, slots=True)
class Injection:
    article_id: UUID
    canaries: list[Canary]
    index_synced: bool


class HoneypotInjector:
    def __init__(
        self,
        factory: SessionFactory,
        embedder: Embedder,
        sync: VectorSync,
        settings: ShieldSettings,
        redis: Redis | None = None,
    ) -> None:
        self._factory = factory
        self._embedder = embedder
        self._sync = sync
        self._settings = settings
        self._redis = redis

    async def _texts(self, count: int) -> list[tuple[str, str]]:
        """(signature, text) per canary, each the most "between clusters" of its candidates."""
        key = signing_key(self._settings)
        now = datetime.now(UTC)
        prefix = self._settings.honeypot_prefix
        slots = [make_signature(key, prefix, now.year) for _ in range(count)]
        candidates = [[canary_text(sig, now) for _ in range(CANDIDATES)] for sig in slots]
        centroids = await load_clusters(self._redis) if self._redis is not None else None
        if centroids is None:
            return [(sig, texts[0]) for sig, texts in zip(slots, candidates, strict=True)]
        flat = [t for texts in candidates for t in texts]
        vectors = np.asarray(await self._embedder.embed(flat), dtype=np.float32)
        if vectors.shape[1] != centroids.shape[1]:
            return [(sig, texts[0]) for sig, texts in zip(slots, candidates, strict=True)]
        chosen: list[tuple[str, str]] = []
        for i, (sig, texts) in enumerate(zip(slots, candidates, strict=True)):
            own = vectors[i * CANDIDATES : (i + 1) * CANDIDATES]
            scores = [betweenness(v, centroids) for v in own]
            chosen.append((sig, texts[int(np.argmax(scores))]))
        return chosen

    async def inject(self, count: int, actor_id: UUID | None) -> Injection:
        if not 1 <= count <= MAX_PER_INJECTION:
            raise ValueError(f"count must be 1..{MAX_PER_INJECTION}")
        texts = await self._texts(count)
        async with self._factory() as session, session.begin():
            author = await _system_author(session)
            article = await _hidden_article(session)
            position = await session.scalar(
                select(func.coalesce(func.max(ArticleBlock.position) + 1, 0)).where(
                    ArticleBlock.article_id == article.id
                )
            )
            canaries: list[Canary] = []
            for offset, (signature, text) in enumerate(texts):
                block = ArticleBlock(
                    id=uuid7(),
                    article_id=article.id,
                    position=int(position or 0) + offset,
                    author_id=author,
                    consent_status=ConsentStatus.ACTIVE,
                    content=text,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    is_honeypot=True,
                )
                session.add(block)
                canaries.append(Canary(block.id, signature))
            await session.flush()
            await AuditService(session).record(
                EventType.HONEYPOT_INJECTED,
                actor_id,
                AuditTarget("wiki_article", str(article.id)),
                {"count": count, "block_ids": [str(c.block_id) for c in canaries]},
            )
            outbox = await self._sync.record(session, [reindex_articles((article.id,))])
        synced = await self._sync.flush(outbox)
        log.info("shield.honeypots_injected", count=count, index_synced=synced)
        return Injection(article.id, canaries, synced)


async def _system_author(session: AsyncSession) -> UUID:
    await session.execute(
        insert(User)
        .values(
            id=uuid7(),
            oidc_issuer=SYSTEM_ISSUER,
            oidc_subject=SYSTEM_SUBJECT,
            display_name="System",
            roles=[],
        )
        .on_conflict_do_nothing(index_elements=["oidc_issuer", "oidc_subject"])
    )
    user_id = await session.scalar(
        select(User.id).where(
            User.oidc_issuer == SYSTEM_ISSUER, User.oidc_subject == SYSTEM_SUBJECT
        )
    )
    assert user_id is not None  # noqa: S101 - inserted above
    return user_id


async def _hidden_article(session: AsyncSession) -> WikiArticle:
    article = await session.scalar(
        select(WikiArticle).where(WikiArticle.is_hidden.is_(True)).with_for_update()
    )
    if article is None:
        article = WikiArticle(
            id=uuid7(),
            title=HIDDEN_TITLE,
            status=ArticleStatus.CANONICAL,
            is_hidden=True,
            last_verified_at=datetime.now(UTC),
        )
        session.add(article)
        await session.flush()
    return article


@dataclass(frozen=True, slots=True)
class StoredCanary:
    block_id: UUID
    signatures: list[str]
    consent_status: ConsentStatus
    created_at: datetime


async def list_canaries(factory: SessionFactory, settings: ShieldSettings) -> list[StoredCanary]:
    key = signing_key(settings)
    async with factory() as session:
        blocks = await session.scalars(
            select(ArticleBlock)
            .where(ArticleBlock.is_honeypot.is_(True))
            .order_by(ArticleBlock.created_at, ArticleBlock.id)
        )
        return [
            StoredCanary(
                b.id, find_signatures(b.content or "", key), b.consent_status, b.created_at
            )
            for b in blocks
        ]


async def trace(
    factory: SessionFactory, settings: ShieldSettings, text: str
) -> tuple[list[str], list[UUID]]:
    """Forensics: the valid canary tokens in ``text`` and the honeypot blocks carrying them."""
    signatures = find_signatures(text, signing_key(settings))
    if not signatures:
        return [], []
    async with factory() as session:
        blocks = list(
            await session.scalars(
                select(ArticleBlock.id).where(
                    ArticleBlock.is_honeypot.is_(True),
                    ArticleBlock.content.regexp_match("|".join(re.escape(s) for s in signatures)),
                )
            )
        )
    return signatures, blocks


# --- Retrieval hook -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HoneypotHit:
    honeypot_ids: list[UUID]
    triggers: int  # within the last 30 days
    risk: float
    locked: bool


def query_hash(question: str) -> str:
    return hashlib.sha256(question.strip().encode()).hexdigest()


class HoneypotMonitor:
    def __init__(
        self,
        redis: Redis,
        factory: SessionFactory,
        risk: RiskStore,
        locker: Locker,
        notifier: Notifier,
        settings: ShieldSettings,
    ) -> None:
        self._redis = redis
        self._factory = factory
        self._risk = risk
        self._locker = locker
        self._notifier = notifier
        self._settings = settings

    async def inspect(
        self, user_id: UUID, question: str, blocks: Sequence[LiveBlock]
    ) -> HoneypotHit | None:
        """None if no honeypot was retrieved. Otherwise the incident is recorded."""
        ids = [b.block_id for b in blocks if b.is_honeypot]
        if not ids:
            return None
        key = f"hp:{user_id}:hits"
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, HITS_TTL_SECONDS, nx=True)
        triggers, _ = await pipe.execute()
        risk = await self._risk.add(user_id, self._settings.honeypot_penalty)
        detail: dict[str, JsonValue] = {
            "honeypot_ids": [str(i) for i in ids],
            "query_sha256": query_hash(question),
            "triggers": int(triggers),
            "risk": round(risk, 4),
        }
        async with self._factory() as session, session.begin():
            await AuditService(session).record(
                EventType.HONEYPOT_TRIGGERED,
                user_id,
                AuditTarget("article_block", str(ids[0])),
                detail,
            )
        await self._notifier.notify(Alert(EventType.HONEYPOT_TRIGGERED.value, user_id, detail))
        log.warning("shield.honeypot_triggered", user_id=str(user_id), triggers=int(triggers))
        locked = int(triggers) >= self._settings.honeypot_lock_after
        if locked:  # an account locked already stays locked: no second record
            await self._locker.lock(
                user_id,
                "honeypot",
                EventType.SHIELD_DECISION,
                {"level": "CRITICAL", "triggers": int(triggers)},
            )
        return HoneypotHit(ids, int(triggers), risk, locked)
