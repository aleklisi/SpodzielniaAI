"""Domains and label vocabulary of the visual atlas (chunk 9.1).

- Domains: ADMIN creates, renames and archives them. An archived domain keeps its labels and
  photos read-only and accepts no new proposals or labels.
- Labels: EXPERT/ADMIN create, edit, approve or reject proposals, merge and deprecate; members
  only propose (``PROPOSED``, visible to the proposer and the experts).
- A merge deprecates the source with ``merged_into_id``. Readers resolve the chain
  (``resolve``), so a member's private annotations never need to be rewritten by an expert
  (they are under RLS); shared atlas regions are re-pointed in place (9.4+).

Methods work in the caller's transaction and write the audit event there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update

from spoldzielnia_ai.atlas.text import clean_name, fold, search_key, slugify
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.enums import VisualDomainStatus, VisualLabelStatus
from spoldzielnia_ai.db.models import User, VisualDomain, VisualLabel

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from uuid import UUID

    from pydantic import JsonValue
    from sqlalchemy.ext.asyncio import AsyncSession

SEARCH_LIMIT = 50
MAX_SYNONYMS = 20
MERGE_DEPTH = 10

# Shared tables whose ``label_id`` a merge re-points (filled by later chunks of Stage 9).
LABEL_REFERENCES: list[tuple[type, str]] = []


class LabelError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _not_found(what: str) -> LabelError:
    return LabelError(f"{what}_not_found", 404)


def _synonyms(values: Iterable[str], name: str) -> list[str]:
    seen = {fold(name)}
    result: list[str] = []
    for raw in values:
        value = clean_name(raw)
        if value and fold(value) not in seen:
            seen.add(fold(value))
            result.append(value[:100])
    if len(result) > MAX_SYNONYMS:
        raise LabelError("too_many_synonyms", 422)
    return result


def _like(q: str) -> str:
    escaped = fold(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class LabelService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    async def _record(
        self,
        event: EventType,
        actor_id: UUID | None,
        target: VisualDomain | VisualLabel,
        **details: JsonValue,
    ) -> None:
        kind = "visual_domain" if isinstance(target, VisualDomain) else "visual_label"
        await self._audit.record(event, actor_id, AuditTarget(kind, str(target.id)), details)

    # --- Domains ------------------------------------------------------------------------------

    async def domains(self) -> Sequence[VisualDomain]:
        return (await self._session.scalars(select(VisualDomain).order_by(VisualDomain.name))).all()

    async def domain(self, domain_id: UUID, *, for_update: bool = False) -> VisualDomain:
        stmt = select(VisualDomain).where(VisualDomain.id == domain_id)
        found = await self._session.scalar(stmt.with_for_update() if for_update else stmt)
        if found is None:
            raise _not_found("domain")
        return found

    async def _active_domain(self, domain_id: UUID) -> VisualDomain:
        domain = await self.domain(domain_id)
        if domain.status != VisualDomainStatus.ACTIVE:
            raise LabelError("domain_archived")
        return domain

    async def create_domain(
        self, actor_id: UUID | None, name: str, description: str | None
    ) -> VisualDomain:
        name = clean_name(name)
        slug = slugify(name)
        if not slug:
            raise LabelError("invalid_name", 422)
        if await self._session.scalar(select(VisualDomain.id).where(VisualDomain.slug == slug)):
            raise LabelError("domain_exists")
        domain = VisualDomain(
            slug=slug, name=name, description=_text(description), created_by=actor_id
        )
        self._session.add(domain)
        await self._session.flush()
        await self._record(EventType.DOMAIN_CREATED, actor_id, domain, name=name)
        return domain

    async def update_domain(
        self,
        actor_id: UUID,
        domain_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        status: VisualDomainStatus | None = None,
    ) -> VisualDomain:
        domain = await self.domain(domain_id, for_update=True)
        changes: dict[str, JsonValue] = {}
        if name is not None and clean_name(name) != domain.name:
            name = clean_name(name)
            slug = slugify(name)
            if not slug:
                raise LabelError("invalid_name", 422)
            clash = select(VisualDomain.id).where(
                VisualDomain.slug == slug, VisualDomain.id != domain.id
            )
            if await self._session.scalar(clash):
                raise LabelError("domain_exists")
            changes["name"] = [domain.name, name]
            domain.name, domain.slug = name, slug  # the slug follows the name
        if description is not None and _text(description) != domain.description:
            domain.description = _text(description)
            changes["description"] = True
        if status is not None and status != domain.status:
            changes["status"] = [domain.status.value, status.value]
            domain.status = status
        if changes:
            await self._session.flush()
            await self._record(EventType.DOMAIN_CHANGED, actor_id, domain, **changes)
        return domain

    # --- Labels -------------------------------------------------------------------------------

    async def label(self, label_id: UUID, *, for_update: bool = False) -> VisualLabel:
        stmt = select(VisualLabel).where(VisualLabel.id == label_id)
        found = await self._session.scalar(stmt.with_for_update() if for_update else stmt)
        if found is None:
            raise _not_found("label")
        return found

    async def search(
        self,
        viewer_id: UUID,
        *,
        domain_id: UUID | None = None,
        q: str = "",
        statuses: Iterable[VisualLabelStatus] | None = None,
        curator: bool = False,
        limit: int = SEARCH_LIMIT,
    ) -> Sequence[VisualLabel]:
        """Members see ACTIVE labels and their own proposals; curators (experts) see any status.
        ``statuses`` narrows it further."""
        stmt = select(VisualLabel)
        if domain_id is not None:
            stmt = stmt.where(VisualLabel.domain_id == domain_id)
        if not curator:
            stmt = stmt.where(
                or_(
                    VisualLabel.status == VisualLabelStatus.ACTIVE,
                    (VisualLabel.status == VisualLabelStatus.PROPOSED)
                    & (VisualLabel.proposed_by == viewer_id),
                )
            )
        if statuses is not None:
            stmt = stmt.where(VisualLabel.status.in_(list(statuses)))
        if fold(q):
            stmt = stmt.where(VisualLabel.search_key.ilike(_like(q), escape="\\"))
        stmt = stmt.order_by(VisualLabel.name).limit(limit)
        return (await self._session.scalars(stmt)).all()

    async def _ensure_unique(self, domain_id: UUID, name: str, exclude: UUID | None = None) -> str:
        """A label's slug is unique per domain; REJECTED proposals don't block a name."""
        slug = slugify(name)
        if not slug:
            raise LabelError("invalid_name", 422)
        stmt = select(VisualLabel.id).where(
            VisualLabel.domain_id == domain_id,
            VisualLabel.slug == slug,
        )
        if exclude is not None:
            stmt = stmt.where(VisualLabel.id != exclude)
        if await self._session.scalar(stmt):
            raise LabelError("label_exists")
        return slug

    async def _free_rejected_slug(self, domain_id: UUID, slug: str) -> None:
        """A rejected proposal keeps its row (audit) but gives its slug back."""
        rejected = await self._session.scalar(
            select(VisualLabel).where(
                VisualLabel.domain_id == domain_id,
                VisualLabel.slug == slug,
                VisualLabel.status == VisualLabelStatus.REJECTED,
            )
        )
        if rejected is not None:
            rejected.slug = f"{slug[:60]}~{str(rejected.id)[-12:]}"[:100]
            await self._session.flush()

    async def _new_label(
        self,
        actor_id: UUID | None,
        domain_id: UUID,
        name: str,
        description: str | None,
        synonyms: Iterable[str],
        status: VisualLabelStatus,
    ) -> VisualLabel:
        await self._active_domain(domain_id)
        name = clean_name(name)
        await self._free_rejected_slug(domain_id, slugify(name))
        clean_synonyms = _synonyms(synonyms, name)
        slug = await self._ensure_unique(domain_id, name)
        proposed = status == VisualLabelStatus.PROPOSED
        label = VisualLabel(
            domain_id=domain_id,
            slug=slug,
            name=name,
            description=_text(description),
            synonyms=clean_synonyms,
            search_key=search_key(name, clean_synonyms),
            status=status,
            proposed_by=actor_id if proposed else None,
            approved_by=None if proposed else actor_id,
        )
        self._session.add(label)
        await self._session.flush()
        return label

    async def propose(
        self, actor_id: UUID, domain_id: UUID, name: str, description: str | None
    ) -> VisualLabel:
        label = await self._new_label(
            actor_id, domain_id, name, description, (), VisualLabelStatus.PROPOSED
        )
        await self._record(EventType.LABEL_PROPOSED, actor_id, label, name=label.name)
        return label

    async def create(
        self,
        actor_id: UUID | None,
        domain_id: UUID,
        name: str,
        description: str | None,
        synonyms: Iterable[str] = (),
    ) -> VisualLabel:
        label = await self._new_label(
            actor_id, domain_id, name, description, synonyms, VisualLabelStatus.ACTIVE
        )
        await self._record(EventType.LABEL_APPROVED, actor_id, label, name=label.name, created=True)
        return label

    async def edit(
        self,
        actor_id: UUID,
        label_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        synonyms: Iterable[str] | None = None,
    ) -> VisualLabel:
        label = await self.label(label_id, for_update=True)
        if label.status in (VisualLabelStatus.DEPRECATED, VisualLabelStatus.REJECTED):
            raise LabelError("label_closed")
        changes: dict[str, JsonValue] = {}
        new_name = clean_name(name) if name is not None else label.name
        new_synonyms = _synonyms(synonyms, new_name) if synonyms is not None else label.synonyms
        if new_name != label.name:
            label.slug = await self._ensure_unique(label.domain_id, new_name, exclude=label.id)
            changes["name"] = [label.name, new_name]
            label.name = new_name
        if synonyms is not None and new_synonyms != label.synonyms:
            label.synonyms = new_synonyms
            changes["synonyms"] = len(new_synonyms)
        if description is not None and _text(description) != label.description:
            label.description = _text(description)
            changes["description"] = True
        if changes:
            label.search_key = search_key(label.name, label.synonyms)
            await self._session.flush()
            await self._record(EventType.LABEL_CHANGED, actor_id, label, **changes)
        return label

    async def _proposal(self, label_id: UUID) -> VisualLabel:
        label = await self.label(label_id, for_update=True)
        if label.status != VisualLabelStatus.PROPOSED:
            raise LabelError("not_proposed")
        return label

    async def approve(self, actor_id: UUID, label_id: UUID) -> VisualLabel:
        label = await self._proposal(label_id)
        await self._active_domain(label.domain_id)
        label.status = VisualLabelStatus.ACTIVE
        label.approved_by = actor_id
        await self._session.flush()
        await self._record(EventType.LABEL_APPROVED, actor_id, label, name=label.name)
        return label

    async def reject(self, actor_id: UUID, label_id: UUID, reason: str | None) -> VisualLabel:
        label = await self._proposal(label_id)
        label.status = VisualLabelStatus.REJECTED
        await self._session.flush()
        await self._record(
            EventType.LABEL_REJECTED, actor_id, label, name=label.name, reason=_text(reason)
        )
        return label

    async def merge(self, actor_id: UUID, label_id: UUID, into_id: UUID) -> VisualLabel:
        """``label_id`` (ACTIVE or PROPOSED) becomes a DEPRECATED alias of ``into_id`` (ACTIVE,
        same domain). Its name joins the target's synonyms, so search still finds it."""
        if label_id == into_id:
            raise LabelError("merge_into_self", 422)
        # Lock both in id order: two opposite merges can't deadlock.
        first, second = sorted((label_id, into_id))
        locked = {first: await self.label(first, for_update=True)}
        locked[second] = await self.label(second, for_update=True)
        source, target = locked[label_id], locked[into_id]
        if source.domain_id != target.domain_id:
            raise LabelError("different_domain", 422)
        if source.status not in (VisualLabelStatus.ACTIVE, VisualLabelStatus.PROPOSED):
            raise LabelError("label_closed")
        if target.status != VisualLabelStatus.ACTIVE:
            raise LabelError("target_not_active")
        source.status = VisualLabelStatus.DEPRECATED
        source.merged_into_id = target.id
        target.synonyms = _synonyms([*target.synonyms, source.name, *source.synonyms], target.name)
        target.search_key = search_key(target.name, target.synonyms)
        for model, column in LABEL_REFERENCES:
            ref = getattr(model, column)
            await self._session.execute(
                update(model).where(ref == source.id).values({column: target.id})
            )
        await self._session.flush()
        await self._record(
            EventType.LABEL_MERGED,
            actor_id,
            source,
            name=source.name,
            into_id=str(target.id),
            into_name=target.name,
        )
        return source

    async def deprecate(self, actor_id: UUID, label_id: UUID) -> VisualLabel:
        label = await self.label(label_id, for_update=True)
        if label.status != VisualLabelStatus.ACTIVE:
            raise LabelError("not_active")
        label.status = VisualLabelStatus.DEPRECATED
        await self._session.flush()
        await self._record(EventType.LABEL_DEPRECATED, actor_id, label, name=label.name)
        return label

    async def resolve(self, label_ids: Iterable[UUID]) -> dict[UUID, VisualLabel]:
        """Each id → the label it stands for now (following merges)."""
        label_ids = list(label_ids)
        rows: dict[UUID, VisualLabel] = {}
        frontier = set(label_ids)
        for _ in range(MERGE_DEPTH + 1):
            missing = frontier - rows.keys()
            if not missing:
                break
            fetched = (
                await self._session.scalars(select(VisualLabel).where(VisualLabel.id.in_(missing)))
            ).all()
            rows.update({row.id: row for row in fetched})
            frontier = {row.merged_into_id for row in fetched if row.merged_into_id is not None}
        result: dict[UUID, VisualLabel] = {}
        for label_id in label_ids:
            current = rows.get(label_id)
            for _ in range(MERGE_DEPTH):
                if current is None or current.merged_into_id is None:
                    break
                current = rows.get(current.merged_into_id)
            if current is not None:
                result[label_id] = current
        return result

    async def user_names(self, user_ids: set[UUID]) -> dict[UUID, str | None]:
        """Display names for the curation list (proposers)."""
        if not user_ids:
            return {}
        rows = await self._session.execute(
            select(User.id, User.display_name, User.username).where(User.id.in_(user_ids))
        )
        return {uid: display or username for uid, display, username in rows.tuples()}

    async def usable(self, label_id: UUID, actor_id: UUID, domain_id: UUID) -> VisualLabel:
        """A label a member may put on a new mark in ``domain_id``: ACTIVE, or their own
        proposal (an expert decides on it before the photo can be approved)."""
        label = (await self.resolve([label_id])).get(label_id)
        if label is None or label.domain_id != domain_id:
            raise LabelError("label_not_found", 404)
        own_proposal = label.status == VisualLabelStatus.PROPOSED and label.proposed_by == actor_id
        if label.status != VisualLabelStatus.ACTIVE and not own_proposal:
            raise LabelError("label_not_usable", 422)
        return label


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
