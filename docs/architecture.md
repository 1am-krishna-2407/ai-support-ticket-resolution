# Architecture

## Overview

```
browser ──► frontend (nginx, :8080) ──/api/──► backend (FastAPI, :8000) ──► postgres + pgvector
                 static SPA                         │
                                                    ├──► LLM: NVIDIA nemotron-3-super-120b-a12b or Groq gpt-oss-120b (OpenAI-compatible chat API) [Phase 4]
                                                    └──► Embeddings: Qwen3-Embedding-0.6B via Hugging Face router (DeepInfra) [Phase 2]
```

- **Frontend**: static React build served by unprivileged nginx. `/api/*` is
  proxied to the backend, so the browser only talks to one origin.
- **Backend**: FastAPI app built by `app.main:create_app`. Layers:
  `api` (routes, dependencies) → `services` (use cases) → `repositories` (SQL)
  and `providers` (external HTTP APIs). `core` holds config, errors, logging,
  middleware, and security.
- **Database**: PostgreSQL 17 with pgvector. Schema is managed by Alembic
  migrations that are run explicitly (`make migrate`), never on import or startup.

## Cross-cutting behaviour (Phase 1)

| Concern | Implementation |
|---|---|
| Configuration | `core/config.py`: one typed `Settings`, fails fast on invalid values and cross-field conflicts |
| Error contract | `{"error": {"code", "message", "details", "request_id"}}` for every error, including 404, 405, 413, 422 and unhandled 500s. No stack traces or input echoes |
| Request IDs | `RequestContextMiddleware`: honours a safe incoming `X-Request-ID`, otherwise generates one; returned on every response and attached to every log record |
| Logging | JSON lines on stdout; keys/tokens/passwords/DB URLs redacted; exceptions logged by type only |
| Body limits | `BodySizeLimitMiddleware` buffers up to `MAX_REQUEST_BODY_BYTES` and returns 413 above that (also for chunked uploads) |
| Admin routes | `core/security.require_admin` (see D10) |

## Data model

| Table | Purpose |
|---|---|
| `documents` | One row per document *version*. `(document_id, version)` unique; status `staged → active → superseded` (or `failed`); a partial unique index allows at most one `active` version per document |
| `document_chunks` | Retrieval units with heading path, content, token count, deterministic `chunk_id`, `embedding vector(N)`, and a generated weighted `search_vector` (heading = A, body = B) |
| `ingestion_jobs` | Job lifecycle and counters, including embedding calls and reused embeddings |
| `evaluation_runs` / `evaluation_cases` | Frozen run config, aggregate metrics, per-case results |
| `request_logs` | Operational metadata only; ticket bodies and LLM payloads are never stored here |

Indexes: HNSW (`vector_cosine_ops`) on embeddings; GIN on `search_vector` and
chunk `metadata`; a partial index on active + enabled documents; plus lookup
indexes on `document_id`, `category`, `status`, `enabled`, `content_hash`.

A test (`tests/integration/test_migrations.py::test_models_match_migrations`)
fails if the ORM models and the migrated schema drift apart.

## Decisions (ADRs)

Status **Accepted** means implemented or binding. **Provisional** means it is
the working default and needs confirmation from the project owner before the
phase that depends on it.

| # | Decision | Status | Notes |
|---|---|---|---|
| D1 | Knowledge source: GitLab docs (`docs.gitlab.com`), curated manifest of ~150–300 pages across 11 categories | Accepted | Selected from the published English sitemap, so every URL exists |
| D2 | Verify licence and robots.txt before any fetch; record the verified text in `docs/licensing.md` and on each document | Accepted | Verified 2026-09-29: CC BY-SA 4.0 (footer `rel="license"` link). robots.txt allows the curated paths, and the fetcher enforces robots.txt on every request |
| D3 | Fetch rendered HTML from the allowlisted domain and parse the main content region | Accepted | Implemented (`fetcher.py`, `parser.py`); see `docs/ingestion.md` |
| D4 | Providers: **LLM = NVIDIA `nvidia/nemotron-3-super-120b-a12b`** (`LLM_PROVIDER=nvidia`, `NEMOTRON_API_KEY`, `https://integrate.api.nvidia.com/v1`) since 2026-09-30, with Groq `openai/gpt-oss-120b` (`LLM_PROVIDER=groq`, `LLM_API_KEY`) still selectable; both through the OpenAI-compatible chat-completions API with strict JSON-schema output. Groq was demoted because its free tier caps gpt-oss-120b at 200,000 tokens per day, about two 10-ticket runs; a per-day 429 now fails fast instead of retrying. Nemotron's reasoning is a chat-template switch (`LLM_ENABLE_THINKING`) and arrives in a separate `reasoning_content` field that is never read; NVIDIA's free tier is paced by requests per minute (`LLM_REQUESTS_PER_MINUTE`, default 30). **Embeddings = `Qwen/Qwen3-Embedding-0.6B`** through the Hugging Face Inference Providers router (`https://router.huggingface.co/deepinfra/v1/openai/embeddings`, authenticated with `HF_TOKEN`). Gemini `gemini-embedding-001` remains selectable with `EMBEDDING_PROVIDER=gemini`. All providers sit behind the `LLMProvider` / `EmbeddingProvider` protocols | Accepted (revised 2026-09-29) | Gemini was dropped as the default because its free tier caps embeddings at 1,000 inputs per day per project, with each batch item counted separately (about a fifth of the corpus). For this model the router maps to DeepInfra: Hugging Face's own `hf-inference` backend reported an error status for it. Qwen3 expects an instruction on queries only (`Instruct: …
Query:…`, `EMBEDDING_QUERY_INSTRUCTION`); documents are embedded as-is. HTTP 402 (credits used up) fails fast into reuse-only ingestion |
| D5 | Embedding dimension **1024** (Qwen3-Embedding-0.6B native size; sent as `dimensions`). Migration `0005` resized the column from 1536 and cleared the Gemini vectors | Accepted (revised 2026-09-29) | Vectors from different models are not comparable, so a model change always means a re-embed. The embedding spec (`Qwen/Qwen3-Embedding-0.6B@1024`) is part of each document's pipeline signature, so the next ingest re-embeds automatically. Ingestion refuses to start if the column dimension and the provider disagree. Vectors are L2-normalised client-side regardless of provider |
| D6 | HNSW index with `vector_cosine_ops` | Accepted | HNSW on `vector` supports ≤ 2000 dimensions; migration `0003` refuses larger values rather than silently skipping the index. A 3072-dim model would need `halfvec` or reduced dimensions |
| D7 | Lexical search: generated `tsvector` + GIN, queried with `websearch_to_tsquery`, plus an exact-phrase pass for quoted error strings | Accepted (revised in Phase 3) | Queries use an OR over the input's lexemes (`to_tsquery`) ranked by `ts_rank_cd`: `websearch_to_tsquery` ANDs every word, so long tickets matched nothing. Identifiers cannot break tsquery syntax |
| D8 | Apply the relevance threshold to a comparable score (reranker score, else max cosine similarity), never to RRF scores | Accepted | Implemented; threshold 0.45 tuned on golden queries (`docs/retrieval.md` §7). An exact-phrase match also passes |
| D9 | Background jobs: FastAPI background task + DB status row + Postgres advisory lock; no Celery or Redis | Accepted | Implemented for ingestion and evaluation, with interrupted-job recovery at startup |
| D10 | Admin protection: `ADMIN_MODE` = `token` \| `local` \| `dev` | Accepted | Implemented and tested. `token` (the default) needs `ADMIN_TOKEN` ≥ 16 chars and uses constant-time comparison. `local` trusts only the socket peer, never `X-Forwarded-For`; behind the bundled nginx proxy the peer is nginx, so `local` effectively blocks proxied admin calls. `dev` is refused when `ENVIRONMENT=production` |
| D11 | Grounding: deterministic checks mandatory; claim-level LLM verification optional (`CLAIM_LEVEL_GROUNDING_ENABLED`) | Accepted | Implemented; `docs/security.md` explains how validation contains a hijacked model |
| D12 | Rate limiting: in-process sliding window keyed by client IP + route group (analysis, search) | Accepted (revised in Phase 8) | Behind nginx the socket peer is the proxy, so the app takes the last `X-Forwarded-For` entry, but only from `TRUSTED_PROXY_CIDRS`; Compose pins the frontend to 172.28.10.10 and trusts only that address. Uvicorn's proxy-header trust stays at `127.0.0.1`, so `request.client` remains the real peer for `ADMIN_MODE=local` |
| D13 | Tokenizer: `tiktoken` with `o200k_base` (`TOKENIZER_ENCODING`), close to gpt-oss's tokenizer, for chunk sizing and context budgets | Accepted | The encoding file is baked into the backend image, so there are no runtime downloads. Each embedding provider declares a safe input size (Qwen3: 8,000; Gemini: 1,800 of its 2,048); the 900-token chunk hard maximum stays well below both |
| D14 | Integration tests use a real Postgres + pgvector (`<db>_test` in the Compose Postgres) | Accepted | Implemented |
