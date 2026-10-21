"""Example atlas domain for development, demos and tests (``spoldzielnia atlas seed-example``).

Nothing here runs in production: the atlas is multi-domain and production domains are created
by admins. Idempotent: existing labels are left alone, missing ones are added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.atlas.text import slugify
from spoldzielnia_ai.db.enums import Role
from spoldzielnia_ai.db.models import User, VisualDomain, VisualLabel

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.db.session import SessionFactory

EXAMPLE_DOMAIN = ("Pszczelarstwo", "Przykładowa dziedzina: pasieka, rodzina pszczela, choroby.")

# (name, description, synonyms)
EXAMPLE_LABELS: list[tuple[str, str, list[str]]] = [
    ("Matka pszczela", "Jedyna płodna samica w rodzinie; dłuższy odwłok.", ["matka", "królowa"]),
    ("Robotnica", "Niepłodna samica, większość rodziny.", []),
    ("Truteń", "Samiec; krępy, duże oczy stykające się na czubku głowy.", ["trutnie"]),
    ("Jajo", "Jajo złożone przez matkę na dnie komórki.", ["jaja"]),
    ("Czerw otwarty", "Larwy w otwartych komórkach.", ["larwy"]),
    ("Czerw kryty", "Poczwarki w komórkach zasklepionych woskiem.", []),
    ("Matecznik", "Komórka, w której wychowuje się matka.", ["mateczniki"]),
    ("Miseczka mateczna", "Zaczątek matecznika, jeszcze bez jaja lub larwy.", []),
    ("Pierzga", "Pyłek zakonserwowany w komórkach.", []),
    ("Miód kryty", "Dojrzały miód zasklepiony woskiem.", []),
    ("Warroza (Varroa destructor)", "Roztocz pasożytujący na pszczołach.", ["warroa"]),
    ("Zgnilec (objawy)", "Zapadnięte, dziurawe zasklepy, ciągnąca się masa.", ["zgnilec"]),
    ("Plaster", "Plaster woskowy z komórkami.", ["plaster woskowy"]),
]


@dataclass(frozen=True, slots=True)
class SeedReport:
    domain_id: UUID
    domain_created: bool
    labels_created: int


async def seed_example(factory: SessionFactory, actor_id: UUID | None = None) -> SeedReport:
    """``actor_id`` is recorded as creator; without it the first admin (or nobody)."""
    name, description = EXAMPLE_DOMAIN
    async with factory() as session, session.begin():
        labels = LabelService(session)
        if actor_id is None:
            actor_id = await session.scalar(
                select(User.id).where(User.roles.contains([Role.ADMIN])).order_by(User.id).limit(1)
            )
        domain = await session.scalar(
            select(VisualDomain).where(VisualDomain.slug == slugify(name))
        )
        created = domain is None
        if domain is None:
            domain = await labels.create_domain(actor_id, name, description)
        existing = set(
            (
                await session.scalars(
                    select(VisualLabel.slug).where(VisualLabel.domain_id == domain.id)
                )
            ).all()
        )
        count = 0
        for label, text, synonyms in EXAMPLE_LABELS:
            if slugify(label) in existing:
                continue
            await labels.create(actor_id, domain.id, label, text, synonyms)
            count += 1
        return SeedReport(domain.id, created, count)
