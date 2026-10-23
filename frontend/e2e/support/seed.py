"""E2E fixtures that the UI cannot create, run inside the api container (6.7):

    docker compose exec -T -e E2E_ARGS='{"action": ...}' api python - < e2e/support/seed.py

- canonical: a CANONICAL article (blocks by the given authors, approved by an expert),
  indexed in Qdrant with the stack's real local embeddings. Co-authored articles have no
  UI (blocks come from promotions), so the Exit Strategy scenario needs this.
- gps_jpeg: a small JPEG carrying GPS EXIF (base64), to upload through the UI;
- gps: decrypt a stored attachment as its owner and report whether GPS EXIF survived.
- reset_shield: clear an account's shield state (risk, honeypot hits, challenges, coverage,
  semantic history) after a lockout scenario, so reruns start clean.
- retire_topic: supersede the CANONICAL articles an earlier run of a scenario left behind
  (blocks by the given author, title matching an ILIKE pattern), through the governance
  transition and the vector outbox, so a rerun's chat answer can only cite this run's article.
- atlas_example: load the example atlas domain (idempotent, Stage 9).
- atlas_jpeg: a JPEG with structure unique to this run (base64), so the atlas's duplicate
  check never matches a photo from an earlier run.

Prints one JSON object on stdout.
"""

import asyncio
import hashlib
import io
import json
import os
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import func, select, text

from spoldzielnia_ai.core.crypto import EnvelopeCipher, StaticKeyProvider, row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.settings import get_settings
from spoldzielnia_ai.db.engine import create_app_engine
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, ReviewDecision
from spoldzielnia_ai.db.models import ArticleBlock, ExpertReview, User, WikiArticle
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.db.session import create_session_factory
from spoldzielnia_ai.files.storage import S3ObjectStorage
from spoldzielnia_ai.llm.config_store import LlmConfigStore
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
from spoldzielnia_ai.vector.client import make_qdrant
from spoldzielnia_ai.vector.embedding import Embedder
from spoldzielnia_ai.vector.generations import GenerationStore
from spoldzielnia_ai.vector.indexing import Indexer
from spoldzielnia_ai.vector.sync import VectorSync
from spoldzielnia_ai.wiki.governance import Action, Actor
from spoldzielnia_ai.wiki.review import change_status
from spoldzielnia_ai.workers.jobs import ArticleJob

GPS_IFD = 0x8825


async def user_id(session, email: str) -> UUID:
    found = await session.scalar(select(User.id).where(User.email == email))
    if found is None:
        raise SystemExit(f"no user {email}: log in once first")
    return found


async def canonical(factory, settings, cipher, args) -> dict:
    article_id = uuid7()
    async with factory() as session, session.begin():
        session.add(WikiArticle(id=article_id, title=args["title"], status=ArticleStatus.CANONICAL))
        await session.flush()
        for position, block in enumerate(args["blocks"]):
            content = block["text"]
            session.add(
                ArticleBlock(
                    id=uuid7(),
                    article_id=article_id,
                    position=position,
                    author_id=await user_id(session, block["email"]),
                    consent_status=ConsentStatus.ACTIVE,
                    content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
        session.add(
            ExpertReview(
                article_id=article_id,
                reviewer_id=await user_id(session, args["approver"]),
                decision=ReviewDecision.APPROVE,
            )
        )
        await session.flush()
        article = await session.get(WikiArticle, article_id)
        article.last_verified_at = func.now()
    qdrant = make_qdrant(settings.qdrant)
    try:
        # The index generations' own models (10.3); waits (is parked) if none serves yet.
        gateway = LiteLLMGateway(settings.llm, source=LlmConfigStore(factory, cipher, settings.llm))
        generations = GenerationStore(factory, cipher, settings.llm, ttl_seconds=0)
        indexer = Indexer(factory, cipher, Embedder(gateway, generations), qdrant)
        await indexer.index_canonical_article(ArticleJob(article_id=article_id), True)
    finally:
        await qdrant.close()
    return {"article_id": str(article_id)}


async def gps(factory, settings, cipher, args) -> dict:
    from PIL import Image

    async with factory() as session:
        owner = await user_id(session, args["email"])
    async with rls_session(factory, owner) as session:
        row = (
            await session.execute(
                text("SELECT object_key, dek_wrapped FROM note_attachment WHERE id = :id"),
                {"id": args["attachment_id"]},
            )
        ).one()
    buffer = io.BytesIO()
    await S3ObjectStorage(settings.s3).get(row.object_key, buffer)
    aad = row_aad("note_attachment", UUID(args["attachment_id"]), owner)
    dek = cipher.unwrap(row.dek_wrapped, aad)
    plain = b"".join(cipher.decrypt_stream(dek, io.BytesIO(buffer.getvalue()), aad))
    with Image.open(io.BytesIO(plain)) as image:
        has_gps = bool(image.getexif().get_ifd(GPS_IFD))
    return {"has_gps": has_gps, "bytes": len(plain)}


async def gps_jpeg(factory, settings, cipher, args) -> dict:
    import base64

    from PIL import Image

    image = Image.new("RGB", (64, 48), (200, 160, 40))
    exif = image.getexif()
    location = exif.get_ifd(GPS_IFD)
    location[1], location[2] = "N", (52.0, 13.0, 0.0)
    location[3], location[4] = "E", (21.0, 1.0, 0.0)
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return {"jpeg": base64.b64encode(out.getvalue()).decode()}


async def reset_shield(factory, settings, cipher, args) -> dict:
    async with factory() as session:
        uid = await user_id(session, args["email"])
    redis = Redis.from_url(settings.redis.url())
    try:
        keys = [f"risk:{uid}", f"hp:{uid}:hits", f"sem:{uid}"]
        async for key in redis.scan_iter(match=f"pow:{uid}:*"):
            keys.append(key)
        async for key in redis.scan_iter(match=f"cov:{uid}:*"):
            keys.append(key)
        async for key in redis.scan_iter(match=f"covatlas:{uid}:*"):
            keys.append(key)
        removed = await redis.delete(*keys) if keys else 0
    finally:
        await redis.aclose()
    async with factory() as session, session.begin():
        user = await session.get(User, uid)
        user.risk_score = 0.0
    return {"removed": removed}


async def retire_topic(factory, settings, cipher, args) -> dict:
    async with factory() as session, session.begin():
        author = await user_id(session, args["email"])
        articles = list(
            await session.scalars(
                select(WikiArticle).where(
                    WikiArticle.status == ArticleStatus.CANONICAL,
                    WikiArticle.title.ilike(args["title"]),
                    WikiArticle.id.in_(
                        select(ArticleBlock.article_id).where(ArticleBlock.author_id == author)
                    ),
                )
            )
        )
        ops = []
        for article in articles:
            ops += await change_status(
                session, article, Action.SUPERSEDE, Actor.SYSTEM, None, {"reason": "e2e rerun"}
            )
        await VectorSync.record(session, ops)  # applied by worker-core's outbox reconciler
    return {"retired": [str(a.id) for a in articles]}


async def atlas_example(factory, settings, cipher, args) -> dict:
    from spoldzielnia_ai.atlas.seed import seed_example

    report = await seed_example(factory)
    return {"domain_id": str(report.domain_id), "labels_created": report.labels_created}


async def atlas_jpeg(factory, settings, cipher, args) -> dict:
    import base64
    import random

    from PIL import Image, ImageDraw

    rng = random.Random(os.urandom(16))  # noqa: S311 - test picture, not a secret
    image = Image.new("RGB", (1600, 1200), (rng.randrange(256), 180, 60))
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        x, y = rng.randrange(1600), rng.randrange(1200)
        shade = tuple(rng.randrange(256) for _ in range(3))
        draw.rectangle((x, y, x + rng.randrange(60, 500), y + rng.randrange(60, 400)), fill=shade)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return {"jpeg": base64.b64encode(out.getvalue()).decode()}


async def main() -> None:
    args = json.loads(os.environ["E2E_ARGS"])
    settings = get_settings()
    engine = create_app_engine(settings.db)
    factory = create_session_factory(engine)
    cipher = EnvelopeCipher(StaticKeyProvider(settings.security.kek.get_secret_value()))
    actions = {
        "canonical": canonical,
        "gps": gps,
        "gps_jpeg": gps_jpeg,
        "reset_shield": reset_shield,
        "retire_topic": retire_topic,
        "atlas_example": atlas_example,
        "atlas_jpeg": atlas_jpeg,
    }
    try:
        result = await actions[args["action"]](factory, settings, cipher, args)
    finally:
        await engine.dispose()
    print(json.dumps(result))


asyncio.run(main())
