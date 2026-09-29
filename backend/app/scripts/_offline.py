"""Settings adjustments for offline scripts (tuning, evaluation, sample runs)."""

from __future__ import annotations

from app.core.config import Settings


def offline_settings(settings: Settings) -> Settings:
    """Batch jobs can afford to wait: use the full retry budget for queries and no
    circuit-breaker cooldown, so intermittent provider refusals do not abort the run."""
    return settings.model_copy(
        update={
            "embedding_query_max_retries": settings.embedding_max_retries,
            "embedding_quota_cooldown_seconds": 0.0,
        }
    )
