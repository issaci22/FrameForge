"""split library storage policy, add original retention, kept-original role and profile lineage

The single ``libraries.output_policy`` value mixed two questions: where the output goes and what
happens to the original. They become separate columns (``output_location``, ``original_handling``,
``retention_days``, ``kept_original_location``), backfilled from ``output_policy`` exactly the way
``build_plan`` interpreted it (an ``output_dir`` library without a folder behaved like ``backup``).
``output_policy`` stays and is kept in sync, so a downgrade still finds a meaningful value.

``retained_originals`` records originals kept for a period after a verified conversion.
``media_files.role`` marks originals that stay in the library after conversion, so later rules
(e.g. a second aging stage) don't convert them again; existing ones are found by fingerprint.
``profiles.based_on`` records which built-in a profile was started from.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import frameforge_server.db.types

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_libraries = sa.table(
    'libraries',
    sa.column('id', sa.Integer),
    sa.column('output_policy', sa.String),
    sa.column('output_path', sa.String),
    sa.column('output_location', sa.String),
    sa.column('original_handling', sa.String),
    sa.column('kept_original_location', sa.String),
)


def _from_legacy(policy: str | None, output_path: str | None) -> tuple[str, str, str]:
    """(output_location, original_handling, kept_original_location). Frozen copy of storage_policy.from_legacy."""
    if policy == 'replace':
        return 'source_folder', 'delete', 'backup'
    if policy == 'alongside':
        return 'source_folder', 'keep', 'in_place'
    if policy == 'output_dir' and output_path:
        return 'folder', 'keep', 'backup'
    return 'source_folder', 'keep', 'backup'


def upgrade() -> None:
    with op.batch_alter_table('libraries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('output_location', sa.String(length=16), nullable=False, server_default='source_folder'))
        batch_op.add_column(sa.Column('original_handling', sa.String(length=16), nullable=False, server_default='keep'))
        batch_op.add_column(sa.Column('retention_days', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('kept_original_location', sa.String(length=16), nullable=False, server_default='backup'))

    conn = op.get_bind()
    for lib_id, policy, output_path in conn.execute(sa.select(_libraries.c.id, _libraries.c.output_policy, _libraries.c.output_path)).all():
        location, handling, kept = _from_legacy(policy, output_path)
        legacy = policy if policy in ('replace', 'backup', 'alongside') or (policy == 'output_dir' and output_path) else 'backup'
        conn.execute(
            sa.update(_libraries)
            .where(_libraries.c.id == lib_id)
            .values(output_location=location, original_handling=handling, kept_original_location=kept, output_policy=legacy)
        )

    with op.batch_alter_table('media_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=16), nullable=False, server_default='source'))
    # Originals that stayed next to their output ("alongside") are recognized by the fingerprint recorded
    # when they were converted.
    conn.execute(
        sa.text(
            "UPDATE media_files SET role = 'kept_original'"
            " WHERE fingerprint IN (SELECT fingerprint FROM processed_fingerprints WHERE kind = 'original')"
            " AND library_id IN (SELECT id FROM libraries WHERE output_policy = 'alongside')"
        )
    )

    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('based_on', sa.String(length=64), nullable=True))

    op.create_table(
        'retained_originals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=True),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('output_job_id', sa.Integer(), nullable=False),
        sa.Column('media_file_id', sa.Integer(), nullable=True),
        sa.Column('original_path', sa.String(length=2048), nullable=False),
        sa.Column('allowed_root', sa.String(length=2048), nullable=False),
        sa.Column('original_size', sa.BigInteger(), nullable=False),
        sa.Column('original_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('original_duration', sa.Float(), nullable=True),
        sa.Column('output_path', sa.String(length=2048), nullable=False),
        sa.Column('output_size', sa.BigInteger(), nullable=True),
        sa.Column('output_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('created_at', frameforge_server.db.types.UTCDateTime(), nullable=False),
        sa.Column('due_at', frameforge_server.db.types.UTCDateTime(), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('checked_at', frameforge_server.db.types.UTCDateTime(), nullable=True),
        sa.Column('deleted_at', frameforge_server.db.types.UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(['library_id'], ['libraries.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('retained_originals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_retained_originals_library_id'), ['library_id'], unique=False)
        batch_op.create_index('ix_retained_originals_state_due', ['state', 'due_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('retained_originals', schema=None) as batch_op:
        batch_op.drop_index('ix_retained_originals_state_due')
        batch_op.drop_index(batch_op.f('ix_retained_originals_library_id'))
    op.drop_table('retained_originals')
    with op.batch_alter_table('profiles', schema=None) as batch_op:
        batch_op.drop_column('based_on')
    with op.batch_alter_table('media_files', schema=None) as batch_op:
        batch_op.drop_column('role')
    # output_policy was kept in sync (never deleting more than the new fields), so it is already correct.
    with op.batch_alter_table('libraries', schema=None) as batch_op:
        batch_op.drop_column('kept_original_location')
        batch_op.drop_column('retention_days')
        batch_op.drop_column('original_handling')
        batch_op.drop_column('output_location')
