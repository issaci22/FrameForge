"""reject outputs larger than the source in every library

Saving space is the point of FrameForge, so an output bigger than its source is now rejected by default
(fail_if_larger=True, max_size_ratio=1.0). Existing libraries stored the old defaults explicitly in their
validation document, so they are switched here too. Users can loosen it again per library.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_libraries = sa.table('libraries', sa.column('id', sa.Integer), sa.column('validation', sa.JSON))


def _set_size_policy(max_size_ratio: float, fail_if_larger: bool) -> None:
    conn = op.get_bind()
    for lib_id, validation in conn.execute(sa.select(_libraries.c.id, _libraries.c.validation)).all():
        doc = dict(validation or {})
        doc['max_size_ratio'] = max_size_ratio
        doc['fail_if_larger'] = fail_if_larger
        conn.execute(sa.update(_libraries).where(_libraries.c.id == lib_id).values(validation=doc))


def upgrade() -> None:
    _set_size_policy(1.0, True)


def downgrade() -> None:
    # The previous defaults. A per-library choice made after upgrading is not restored.
    _set_size_policy(1.05, False)
