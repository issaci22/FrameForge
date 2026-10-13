"""excluded folders per library, and a recorded stop request per job

``libraries.exclude_folders`` lists folders inside a library that are never scanned or converted (validated
absolute container paths, like ``paths``).

``jobs.cancel_requested_at`` records that the user asked to stop a job. Requeue paths (node lost, rejected,
transient failure) end such a job as cancelled instead of queueing it again, and the rule engine doesn't
queue the same file and profile again until the user retries it. Existing cancelled jobs are left without it:
nobody can tell any more whether a user or a node shutdown cancelled them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import frameforge_server.db.types

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('libraries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('exclude_folders', sa.JSON(), nullable=False, server_default='[]'))
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cancel_requested_at', frameforge_server.db.types.UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('cancel_requested_at')
    with op.batch_alter_table('libraries', schema=None) as batch_op:
        batch_op.drop_column('exclude_folders')
