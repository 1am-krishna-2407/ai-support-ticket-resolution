# Architecture

## Overview

```
browser ──► frontend (nginx, :8080) ──/api/──► backend (FastAPI, :8000) ──► postgres + pgvector
                 static SPA                         │
                                                    ├──► LLM provider (OpenAI-compatible)      [Phase 4]
                                                    └──► embedding provider (OpenAI-compatible) [Phase 2]
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
| D1 | Knowledge source: GitLab docs (`docs.gitlab.com`), curated manifest of ~150–300 pages across 11 categories | Provisional | Confirm before Phase 2. `ALLOWED_SOURCE_DOMAINS` defaults to `docs.gitlab.com` |
| D2 | Verify licence and robots.txt before any fetch; record the verified text in `docs/licensing.md` and on each document | Accepted | Not yet verified. This is a Phase 2 entry gate |
| D3 | Fetch rendered HTML from the allowlisted domain and parse the main content region | Accepted | Phase 2 |
| D4 | One OpenAI-compatible HTTP client (`httpx`) for LLM and embeddings, with configurable base URL, model, and key | Accepted | Provider and model choice still open |
| D5 | Embedding dimension is fixed by config (`EMBEDDING_DIMENSION`, default 1536) and applied when migration `0002` creates `vector(N)` | Provisional | Changing it later needs a new migration and a full re-embed. `embedding_model` is stored per chunk |
| D6 | HNSW index with `vector_cosine_ops` | Accepted | HNSW on `vector` supports ≤ 2000 dimensions; migration `0003` refuses larger values rather than silently skipping the index. A 3072-dim model would need `halfvec` or reduced dimensions |
| D7 | Lexical search: generated `tsvector` + GIN, queried with `websearch_to_tsquery`, plus an exact-phrase pass for quoted error strings | Accepted | Column and index exist; queries are Phase 3 |
| D8 | Apply the relevance threshold to a comparable score (reranker score, else max cosine similarity), never to RRF scores | Accepted | Phase 3 |
| D9 | Background jobs: FastAPI background task + DB status row + Postgres advisory lock; no Celery or Redis | Accepted | Phase 2 |
| D10 | Admin protection: `ADMIN_MODE` = `token` \| `local` \| `dev` | Accepted | Implemented and tested. `token` (the default) needs `ADMIN_TOKEN` ≥ 16 chars and uses constant-time comparison. `local` trusts only the socket peer, never `X-Forwarded-For`; behind the bundled nginx proxy the peer is nginx, so `local` effectively blocks proxied admin calls. `dev` is refused when `ENVIRONMENT=production` |
| D11 | Grounding: deterministic checks mandatory; claim-level LLM verification optional (`CLAIM_LEVEL_GROUNDING_ENABLED`) | Accepted | Phase 4 |
| D12 | Rate limiting: in-process token bucket keyed by client IP + route class | Accepted | Phase 8. Uvicorn only trusts proxy headers from `127.0.0.1`, so behind nginx the client IP needs revisiting then |
| D13 | Tokenizer: `tiktoken` (`TOKENIZER_ENCODING`) | Accepted | Phase 2 |
| D14 | Integration tests use a real Postgres + pgvector (`<db>_test` in the Compose Postgres) | Accepted | Implemented |
