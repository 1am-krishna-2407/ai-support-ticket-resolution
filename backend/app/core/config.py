"""Typed application settings.

Every tunable value lives here and is read from the environment (or a `.env`
file). Nothing else in the codebase should hardcode timeouts, limits, model
names, or thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AdminMode(StrEnum):
    """How admin-only routes (ingestion/evaluation triggers) are protected.

    token: requires an `X-Admin-Token` header matching ADMIN_TOKEN.
    local: only requests from loopback addresses are allowed.
    dev:   no protection. Refused when ENVIRONMENT=production.
    """

    TOKEN = "token"  # noqa: S105 (mode name, not a credential)
    LOCAL = "local"
    DEV = "dev"


class EmbeddingProviderName(StrEnum):
    HUGGINGFACE = "huggingface"
    GEMINI = "gemini"


class LLMProviderName(StrEnum):
    GROQ = "groq"
    NVIDIA = "nvidia"


@dataclass(frozen=True)
class _LLMDefaults:
    base_url: str
    model: str
    tokens_per_minute: int | None  # client-side pacing to the provider's free-tier limits
    requests_per_minute: int | None
    # Added to every request's output limit: hidden reasoning counts toward it.
    reasoning_tokens: int
    timeout_seconds: float


_LLM_DEFAULTS = {
    # Groq free tier for gpt-oss-120b: 8,000 tokens/minute (and 200,000 tokens/day).
    LLMProviderName.GROQ: _LLMDefaults(
        "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", 8000, None, 0, 60.0
    ),
    # NVIDIA API catalog (build.nvidia.com) free tier: limited by requests/minute (~40).
    LLMProviderName.NVIDIA: _LLMDefaults(
        "https://integrate.api.nvidia.com/v1",
        "nvidia/nemotron-3-super-120b-a12b",
        None,
        30,
        4000,  # triage at 1,200 was truncated by reasoning on 7 of 10 real tickets
        180.0,
    ),
}

_DEFAULT_EMBEDDING_BASE_URLS = {
    EmbeddingProviderName.HUGGINGFACE: "https://router.huggingface.co/deepinfra/v1/openai",
    EmbeddingProviderName.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
}


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = "Support Resolution Platform"
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:8080"]
    )
    max_request_body_bytes: int = Field(default=256_000, gt=0)

    # --- Database ----------------------------------------------------------
    database_url: SecretStr
    db_pool_size: int = Field(default=10, gt=0)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_statement_timeout_ms: int = Field(default=15_000, gt=0)

    # --- Admin protection --------------------------------------------------
    admin_mode: AdminMode = AdminMode.TOKEN
    admin_token: SecretStr | None = None

    # --- LLM provider (OpenAI-compatible chat completions) --------------------
    # groq: gpt-oss-120b with LLM_API_KEY. nvidia: Nemotron via the NVIDIA API catalog,
    # authenticated with NEMOTRON_API_KEY (falls back to LLM_API_KEY).
    llm_provider: LLMProviderName = LLMProviderName.GROQ
    # Empty means the provider's default (see _LLM_DEFAULTS).
    llm_base_url: str = ""
    llm_api_key: SecretStr | None = None
    nemotron_api_key: SecretStr | None = None
    llm_model: str = ""
    # gpt-oss models are reasoning models; this bounds hidden reasoning tokens.
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"
    # Nemotron reasoning ("thinking") on/off; its reasoning text is never returned or logged.
    llm_enable_thinking: bool = True
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    # Reasoning tokens count toward this limit on gpt-oss; 1,500 truncated some drafts.
    llm_max_output_tokens: int = Field(default=2500, gt=0)
    # Unset means the provider's default.
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_reasoning_tokens: int = Field(default=0, ge=0)
    llm_max_retries: int = Field(default=2, ge=0, le=8)
    # Client-side pacing to the provider's limits. Each request reserves prompt + max output
    # tokens. Unset means the provider's default; None disables.
    llm_tokens_per_minute: int | None = Field(default=None, gt=0)
    llm_requests_per_minute: int | None = Field(default=None, gt=0)
    llm_seed: int | None = 7

    # --- Embedding provider -----------------------------------------------
    # huggingface: OpenAI-style embeddings via the Hugging Face Inference Providers router
    #              (Qwen3-Embedding-0.6B served by DeepInfra), authenticated with HF_TOKEN.
    # gemini:      Gemini native batchEmbedContents, authenticated with EMBEDDING_API_KEY.
    embedding_provider: EmbeddingProviderName = EmbeddingProviderName.HUGGINGFACE
    embedding_base_url: str | None = None  # None = the provider's default (see below)
    embedding_api_key: SecretStr | None = None
    hf_token: SecretStr | None = None
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    # Qwen3 embeddings expect queries (not documents) to carry a task instruction.
    embedding_query_instruction: str = (
        "Given a technical support ticket or question, retrieve documentation passages "
        "that help resolve it"
    )
    # The pgvector column is created with this dimension at migration time; changing it
    # needs a migration (0005 resizes to this value) and a re-embed. HNSW allows <= 2000.
    embedding_dimension: int = Field(default=1024, gt=0, le=2000)
    # Gemini accepts at most 100 inputs per batch; checked below.
    embedding_batch_size: int = Field(default=64, gt=0, le=256)
    embedding_max_requests_per_minute: int = Field(default=60, gt=0)
    embedding_concurrency: int = Field(default=2, gt=0, le=16)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0)
    embedding_max_retries: int = Field(default=5, ge=0, le=8)
    # Interactive search retries less so a failing provider degrades quickly; offline
    # scripts (tuning, evaluation) raise this to embedding_max_retries.
    embedding_query_max_retries: int = Field(default=1, ge=0, le=8)
    # After the provider reports exhausted quota/credits, fail fast (no requests) for this
    # long, so searches degrade immediately instead of waiting through retries.
    embedding_quota_cooldown_seconds: float = Field(default=300.0, ge=0)

    # --- Knowledge ingestion -----------------------------------------------
    allowed_source_domains: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["docs.gitlab.com"]
    )
    manifest_path: str = "knowledge/manifest/sources.json"
    evaluation_dataset_path: str = "evaluation/cases.json"
    raw_storage_dir: str = "knowledge/raw"
    fetch_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    fetch_read_timeout_seconds: float = Field(default=20.0, gt=0)
    fetch_max_redirects: int = Field(default=5, ge=0, le=10)
    fetch_max_response_bytes: int = Field(default=5_000_000, gt=0)
    fetch_max_retries: int = Field(default=3, ge=0, le=8)
    fetch_min_interval_seconds: float = Field(default=1.0, ge=0)
    fetch_user_agent: str = "SupportResolutionPlatform/0.1 (+independent research project)"
    chunk_target_tokens: int = Field(default=450, gt=0)
    chunk_overlap_tokens: int = Field(default=60, ge=0)
    chunk_hard_max_tokens: int = Field(default=900, gt=0)
    # o200k_base matches the gpt-oss tokenizer family closely enough for budgeting.
    tokenizer_encoding: str = "o200k_base"

    # --- Retrieval ---------------------------------------------------------
    retrieval_candidate_k: int = Field(default=40, gt=0, le=500)
    retrieval_top_k: int = Field(default=8, gt=0, le=50)
    retrieval_rrf_k: int = Field(default=60, gt=0)
    retrieval_semantic_weight: float = Field(default=1.0, ge=0)
    retrieval_lexical_weight: float = Field(default=1.0, ge=0)
    # Cosine similarity for Qwen3-Embedding-0.6B@1024, tuned on evaluation/golden_queries.json
    # (docs/retrieval.md §7): out-of-scope best <= 0.355, weakest relevant >= 0.591.
    retrieval_relevance_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    retrieval_max_chunks_per_document: int = Field(default=3, gt=0)
    retrieval_max_query_chars: int = Field(default=4000, gt=0)
    reranker_enabled: bool = False

    # --- Generation / grounding --------------------------------------------
    # Evidence budget per draft; sized so prompt + output fit a free-tier minute budget.
    context_max_tokens: int = Field(default=3500, gt=0)
    classification_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    classification_ambiguity_margin: float = Field(default=0.15, ge=0.0, le=1.0)
    claim_level_grounding_enabled: bool = False

    # --- Ticket input limits -----------------------------------------------
    ticket_title_max_chars: int = Field(default=300, gt=0)
    ticket_description_max_chars: int = Field(default=20_000, gt=0)

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_analyze_per_minute: int = Field(default=20, gt=0)
    rate_limit_search_per_minute: int = Field(default=60, gt=0)

    # --- Observability / privacy -------------------------------------------
    log_llm_payloads: bool = False
    persist_ticket_bodies: bool = False

    @field_validator("cors_origins", "allowed_source_domains", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("allowed_source_domains")
    @classmethod
    def _normalize_domains(cls, value: list[str]) -> list[str]:
        domains = [d.lower().rstrip(".") for d in value]
        for d in domains:
            if "/" in d or ":" in d or " " in d:
                raise ValueError(f"allowed_source_domains entry must be a bare hostname: {d!r}")
        return domains

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log_level: {value!r}")
        return level

    @model_validator(mode="after")
    def _cross_field_checks(self) -> Self:
        defaults = _LLM_DEFAULTS[self.llm_provider]
        self.llm_base_url = self.llm_base_url or defaults.base_url
        self.llm_model = self.llm_model or defaults.model
        if "llm_tokens_per_minute" not in self.model_fields_set:
            self.llm_tokens_per_minute = defaults.tokens_per_minute
        if "llm_reasoning_tokens" not in self.model_fields_set:
            self.llm_reasoning_tokens = defaults.reasoning_tokens
        if "llm_timeout_seconds" not in self.model_fields_set:
            self.llm_timeout_seconds = defaults.timeout_seconds
        if "llm_requests_per_minute" not in self.model_fields_set:
            self.llm_requests_per_minute = defaults.requests_per_minute
        if self.admin_mode is AdminMode.TOKEN:
            token = self.admin_token.get_secret_value() if self.admin_token else ""
            if len(token) < 16:
                raise ValueError("ADMIN_MODE=token requires ADMIN_TOKEN of at least 16 characters")
        if self.admin_mode is AdminMode.DEV and self.environment is Environment.PRODUCTION:
            raise ValueError("ADMIN_MODE=dev is not allowed when ENVIRONMENT=production")
        if self.chunk_overlap_tokens >= self.chunk_target_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_TARGET_TOKENS")
        if self.chunk_hard_max_tokens < self.chunk_target_tokens:
            raise ValueError("CHUNK_HARD_MAX_TOKENS must be >= CHUNK_TARGET_TOKENS")
        if self.retrieval_top_k > self.retrieval_candidate_k:
            raise ValueError("RETRIEVAL_TOP_K must be <= RETRIEVAL_CANDIDATE_K")
        if not self.allowed_source_domains:
            raise ValueError("ALLOWED_SOURCE_DOMAINS must not be empty")
        if (
            self.embedding_provider is EmbeddingProviderName.GEMINI
            and self.embedding_batch_size > 100
        ):
            raise ValueError("EMBEDDING_BATCH_SIZE must be <= 100 for the Gemini provider")
        return self

    @property
    def resolved_embedding_base_url(self) -> str:
        if self.embedding_base_url:
            return self.embedding_base_url
        return _DEFAULT_EMBEDDING_BASE_URLS[self.embedding_provider]

    @property
    def resolved_llm_api_key(self) -> SecretStr | None:
        """NEMOTRON_API_KEY for NVIDIA (LLM_API_KEY may hold another provider's key)."""
        key = self.llm_api_key
        if self.llm_provider is LLMProviderName.NVIDIA:
            key = self.nemotron_api_key or self.llm_api_key
        return key if key is not None and key.get_secret_value() else None

    @property
    def resolved_embedding_api_key(self) -> SecretStr | None:
        """HF_TOKEN for Hugging Face (EMBEDDING_API_KEY may hold another provider's key)."""
        if self.embedding_provider is EmbeddingProviderName.HUGGINGFACE:
            return self.hf_token or self.embedding_api_key
        return self.embedding_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
