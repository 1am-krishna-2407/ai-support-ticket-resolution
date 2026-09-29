"""Add documents.pipeline_signature.

A document is re-processed when either its content hash or its pipeline signature
(parser version, chunking settings, tokenizer, embedding spec, manifest metadata)
differs from the active version, so changing chunk sizes or the embedding model
cannot silently leave stale chunks in place.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("pipeline_signature", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "pipeline_signature")
