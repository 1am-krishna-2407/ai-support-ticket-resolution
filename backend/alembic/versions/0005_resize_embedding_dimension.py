"""Resize document_chunks.embedding to EMBEDDING_DIMENSION (switch to Qwen3-Embedding).

Vectors from different embedding models live in incompatible spaces, so existing
embeddings are cleared (and their `embedding_model` spec with them) and the column is
resized. The next ingestion re-embeds every document automatically, because the
embedding spec is part of each document's pipeline signature. No-op when the column
already has the configured dimension.

Downgrade does not restore the previous vectors (they are gone); it leaves the column
as is. To change dimension again later, add a new revision that calls `resize()`.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HNSW_MAX_DIMENSIONS = 2000


def _target_dimension() -> int:
    override = op.get_context().config.attributes.get("embedding_dimension")  # type: ignore[union-attr]
    if override:
        return int(override)
    from app.core.config import get_settings

    return get_settings().embedding_dimension


def resize(target: int) -> None:
    bind = op.get_bind()
    current = bind.exec_driver_sql(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
    ).scalar_one()
    if current == target:
        return
    if not 0 < target <= HNSW_MAX_DIMENSIONS:
        raise RuntimeError(
            f"embedding dimension {target} is outside 1..{HNSW_MAX_DIMENSIONS} (HNSW limit)"
        )
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.execute("UPDATE document_chunks SET embedding = NULL, embedding_model = NULL")
    op.execute(f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({int(target)})")
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def upgrade() -> None:
    resize(_target_dimension())


def downgrade() -> None:
    # Cleared vectors cannot be restored; the column keeps its current dimension.
    pass
