"""ORM models. Importing this package registers every table on ``Base.metadata``."""

from spoldzielnia_ai.db.base import Base
from spoldzielnia_ai.db.models.atlas import VisualDomain, VisualLabel
from spoldzielnia_ai.db.models.audit import SecurityAuditLog
from spoldzielnia_ai.db.models.llm import LlmConfigRevision, LlmModel, LlmProvider, LlmRouting
from spoldzielnia_ai.db.models.outbox import VectorOutbox
from spoldzielnia_ai.db.models.private_note import NoteAttachment, NoteDerivative, PrivateNote
from spoldzielnia_ai.db.models.promotion import Promotion
from spoldzielnia_ai.db.models.user import User
from spoldzielnia_ai.db.models.wiki import ArticleBlock, ExpertReview, WikiArticle

__all__ = [
    "ArticleBlock",
    "Base",
    "ExpertReview",
    "LlmConfigRevision",
    "LlmModel",
    "LlmProvider",
    "LlmRouting",
    "NoteAttachment",
    "NoteDerivative",
    "PrivateNote",
    "Promotion",
    "SecurityAuditLog",
    "User",
    "VectorOutbox",
    "VisualDomain",
    "VisualLabel",
    "WikiArticle",
]
