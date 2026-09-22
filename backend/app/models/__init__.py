"""
SQLAlchemy models package.

Import all models here so Alembic can discover them via ``Base.metadata``.
"""

from app.models.base import Base, TimestampMixin, SoftDeleteMixin, generate_uuidv7

# Import all model modules so their table metadata is registered on Base
from app.models import user  # noqa: F401
from app.models import content  # noqa: F401
from app.models import collaboration  # noqa: F401
from app.models import reputation  # noqa: F401
from app.models import embedding  # noqa: F401
from app.models import notification  # noqa: F401
from app.models import messaging  # noqa: F401
from app.models import social  # noqa: F401
from app.models import analytics  # noqa: F401
from app.models import streaming  # noqa: F401

__all__ = [
    "Base",
    "TimestampMixin",
    "SoftDeleteMixin",
    "generate_uuidv7",
]