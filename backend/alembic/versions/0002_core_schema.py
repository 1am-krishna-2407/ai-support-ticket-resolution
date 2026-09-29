"""Core schema: documents, chunks, ingestion jobs, evaluation runs/cases, request logs.

The chunk `embedding` column is created as `vector(EMBEDDING_DIMENSION)`, read
from settings at migration time. Changing the embedding dimension later needs a
new migration plus a full re-embed (see docs/architecture.md, ADR D5).

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _embedding_dimension() -> int:
    override = op.get_context().config.attributes.get("embedding_dimension")  # type: ignore[union-attr]
    if override:
        return int(override)
    from app.core.config import get_settings

    return get_settings().embedding_dimension


def _uuid_pk() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _created_at() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    dim = _embedding_dimension()

    op.create_table(
        "ingestion_jobs",
        _uuid_pk(),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("total_documents", sa.Integer(), nullable=False),
        sa.Column("processed_documents", sa.Integer(), nullable=False),
        sa.Column("changed_documents", sa.Integer(), nullable=False),
        sa.Column("failed_documents", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("embedding_calls", sa.Integer(), nullable=False),
        sa.Column("reused_embeddings", sa.Integer(), nullable=False),
        sa.Column(
            "error_summary",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'validating', 'indexing', 'publishing', "
            "'completed', 'failed', 'cancelled')",
            name="status_valid",
        ),
    )
    op.create_index("ix_ingestion_jobs_status", "ingestion_jobs", ["status"])
    op.create_index("ix_ingestion_jobs_created_at", "ingestion_jobs", ["created_at"])

    op.create_table(
        "documents",
        _uuid_pk(),
        sa.Column("document_id", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("canonical_url", sa.String(2000), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("license", sa.String(500), nullable=False),
        sa.Column("source_version", sa.String(100)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("raw_path", sa.String(1000)),
        sa.Column(
            "ingestion_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.UniqueConstraint("document_id", "version"),
        sa.CheckConstraint(
            "status IN ('staged', 'active', 'superseded', 'failed')", name="status_valid"
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    op.create_index("ix_documents_document_id", "documents", ["document_id"])
    op.create_index("ix_documents_category", "documents", ["category"])
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_enabled", "documents", ["enabled"])
    op.create_index(
        "uq_documents_one_active_version",
        "documents",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "document_chunks",
        _uuid_pk(),
        sa.Column("chunk_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "document_pk",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", sa.String(200), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("section", sa.String(500)),
        sa.Column("subsection", sa.String(500)),
        sa.Column("heading_path", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("canonical_url", sa.String(2000), nullable=False),
        sa.Column("anchor", sa.String(500)),
        sa.Column("has_code", sa.Boolean(), nullable=False),
        sa.Column("is_split_oversize", sa.Boolean(), nullable=False),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column("embedding", Vector(dim)),
        sa.Column(
            "metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        _created_at(),
        sa.UniqueConstraint("document_pk", "chunk_index"),
        sa.CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
        sa.CheckConstraint("token_count > 0", name="token_count_positive"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"])
    op.create_index("ix_document_chunks_category", "document_chunks", ["category"])

    op.create_table(
        "evaluation_runs",
        _uuid_pk(),
        sa.Column("name", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("dataset_version", sa.String(100)),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')", name="status_valid"
        ),
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_index("ix_evaluation_runs_created_at", "evaluation_runs", ["created_at"])

    op.create_table(
        "evaluation_cases",
        _uuid_pk(),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(100), nullable=False),
        sa.Column("case_type", sa.String(64), nullable=False),
        sa.Column("expected", postgresql.JSONB(), nullable=False),
        sa.Column("actual", postgresql.JSONB()),
        sa.Column("metrics", postgresql.JSONB()),
        sa.Column("passed", sa.Boolean()),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("error", sa.Text()),
        _created_at(),
        sa.UniqueConstraint("run_id", "case_id"),
    )
    op.create_index("ix_evaluation_cases_run_id", "evaluation_cases", ["run_id"])

    op.create_table(
        "request_logs",
        _uuid_pk(),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("route_class", sa.String(50)),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("stage_timings", postgresql.JSONB()),
        sa.Column("outcome", postgresql.JSONB()),
        _created_at(),
    )
    op.create_index("ix_request_logs_request_id", "request_logs", ["request_id"])
    op.create_index("ix_request_logs_route_class", "request_logs", ["route_class"])
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("request_logs")
    op.drop_table("evaluation_cases")
    op.drop_table("evaluation_runs")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("ingestion_jobs")
