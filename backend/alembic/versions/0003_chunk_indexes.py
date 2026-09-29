"""Chunk search indexes: HNSW (cosine) on embeddings, generated tsvector + GIN.

pgvector's HNSW index supports at most 2000 dimensions for the `vector` type;
this migration refuses larger dimensions instead of silently building no index.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HNSW_MAX_DIMENSIONS = 2000

# Frozen copy of app.db.models.SEARCH_VECTOR_EXPRESSION at the time of this revision.
SEARCH_VECTOR_EXPRESSION = (
    "setweight(to_tsvector('english', coalesce(heading_path, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(content, '')), 'B')"
)


def upgrade() -> None:
    bind = op.get_bind()
    dim = bind.exec_driver_sql(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
    ).scalar_one()
    if dim > HNSW_MAX_DIMENSIONS:
        raise RuntimeError(
            f"embedding dimension {dim} exceeds the HNSW limit of {HNSW_MAX_DIMENSIONS}; "
            "choose a smaller embedding dimension (see docs/architecture.md, ADR D5/D6)"
        )

    op.execute(
        "ALTER TABLE document_chunks ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({SEARCH_VECTOR_EXPRESSION}) STORED"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_search_vector ON document_chunks USING gin (search_vector)"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_doc_version "
        "ON document_chunks (document_id, document_version)"
    )
    op.execute("CREATE INDEX ix_document_chunks_metadata ON document_chunks USING gin (metadata)")
    # Retrieval filters on active + enabled documents on every query.
    op.execute(
        "CREATE INDEX ix_documents_active_enabled ON documents (id) "
        "WHERE status = 'active' AND enabled"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_documents_active_enabled")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_metadata")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_doc_version")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_search_vector")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS search_vector")
