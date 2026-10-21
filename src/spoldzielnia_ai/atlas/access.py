"""Who may see an atlas photo (A5): one rule for images, pages and citations.

- Nobody sees a REVOKED photo (it has no content left anyway).
- Its author sees it in any state (draft, in review, sent back, rejected, suspended).
- Experts with MFA see photos under review and approved ones that are not withdrawn.
- Every member sees APPROVED photos whose author's consent is ACTIVE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus, Role

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from spoldzielnia_ai.db.models import AtlasImage

REVIEWABLE = frozenset({AtlasImageStatus.IN_REVIEW, AtlasImageStatus.REVISION_REQUIRED})


def is_public(image: AtlasImage) -> bool:
    return (
        image.status == AtlasImageStatus.APPROVED and image.consent_status == ConsentStatus.ACTIVE
    )


def can_view(image: AtlasImage, viewer_id: UUID, roles: Iterable[Role], mfa: bool) -> bool:
    if image.consent_status == ConsentStatus.REVOKED:
        return False
    if image.author_id == viewer_id or is_public(image):
        return True
    expert = mfa and bool({Role.EXPERT, Role.ADMIN} & set(roles))
    return (
        expert
        and image.consent_status == ConsentStatus.ACTIVE
        and image.status in REVIEWABLE | {AtlasImageStatus.APPROVED}
    )
