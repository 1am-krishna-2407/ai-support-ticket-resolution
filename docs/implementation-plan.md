# Implementation Plan: AI-Powered Technical Support Ticket Resolution Platform

Companion to `ai_support_ticket_resolution_platform_spec.md` (the source of truth).
This plan turns the spec into an ordered, verifiable build. Section references like **[§12]** point to the spec.

> **Revision 2026-09-29 (embeddings).** Embeddings moved to **`Qwen/Qwen3-Embedding-0.6B`
> (1024 dimensions) through the Hugging Face Inference Providers router** (served by DeepInfra,
> authenticated with `HF_TOKEN`). Gemini's free tier capped embeddings at 1,000 inputs per day.
> Gemini remains selectable with `EMBEDDING_PROVIDER=gemini`. D4, D5, and D13 are updated in
> `docs/architecture.md`, which supersedes the Gemini-specific details below.
>
> **Revision 2026-09-29 (provider choice).** The LLM is **Groq `openai/gpt-oss-120b`**
> (OpenAI-compatible chat API) and embeddings are **Google Gemini `gemini-embedding-001`
> at 1536 dimensions** (native Gemini REST API). Groq serves no embedding models, which
> is why the two roles use different providers. Changed items: D1, D2, D4, D5, D13,
> Phase 0 status, §2.5, §4.1, and the risk table. Rationale: `docs/architecture.md`.

---

## 0. How to Use This Plan

- Work **phase by phase**. Each phase has deliverables, tasks, tests, and an **exit gate**. Do not start the next phase until the gate passes.
- Follow the spec's dependency order **[§110]**: database → migrations → models → repositories → providers → ingestion → retrieval → classification → generation → grounding → API → tests → frontend → evaluation → observability → hardening → deployment validation.
- The frontend is built **against real, working APIs**, never imaginary responses **[§110, §49]**.
- Every phase ends with a commit tagged `phase-N-complete` and a short changelog entry in `docs/CHANGELOG.md`.
- Estimates assume one developer working full-time; adjust freely.

| Phase | Name | Est. | Exit gate (summary) | Status |
|---|---|---|---|---|
| 0 | Decisions & prerequisites | 0.5-1 d | Provider keys work; source licence verified | Done |
| 1 | Foundation | 2-3 d | `docker compose up` gives healthy frontend, backend, Postgres with pgvector; migration runs | Done (`phase-1-complete`) |
| 2 | Knowledge ingestion | 5-7 d | Real docs fetched, chunked, embedded, stored, versioned; re-run skips unchanged docs | Done (`phase-2-complete`) |
| 3 | Retrieval | 3-4 d | Vector, lexical, hybrid retrieval via API with scores and threshold | Done (`phase-3-complete`) |
| 4 | AI resolution | 5-6 d | Ticket → classification → priority → grounded, validated draft; all failure paths tested with fakes | Done (`phase-4-complete`) |
| 5 | API completion & contracts | 2 d | All 15 routes implemented, error contract, OpenAPI accurate | Done (`phase-5-complete`) |
| 6 | Frontend | 6-8 d | All 7 pages on real APIs, all UI states handled | Done (`phase-6-complete`) |
| 7 | Evaluation | 4-5 d | Cases authored, runner, metrics, experiments stored, UI shows real metrics | Done (`phase-7-complete`) |
| 8 | Security & observability hardening | 3-4 d | SSRF, injection, limits, admin protection, structured logs, all security tests pass | Done (`phase-8-complete`) |
| 9 | Testing, docs, release | 3-4 d | Clean-room Docker acceptance test passes; audit clean; docs complete | Done (`phase-9-complete`) |

Total: roughly 35-45 working days.

---

## 1. Key Technical Decisions

Recorded as ADRs in `docs/architecture.md`.

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Knowledge source | GitLab docs (`docs.gitlab.com`), curated manifest of ~150-300 pages across the 11 categories, selected from the published English sitemap | Large enough for realistic retrieval, small enough to embed cheaply |
| D2 | Licence/terms | **Verified:** CC BY-SA 4.0 (`https://creativecommons.org/licenses/by-sa/4.0/`, footer `rel="license"` link). robots.txt disallows only `/review-mr`, `/upstream-review-mr`, and versioned archives (`/1*`, `/2*`, `/3*`); the fetcher enforces robots.txt. Recorded in `docs/licensing.md` and in each document's `license` field | Spec forbids inventing licence values **[§8.2]** |
| D3 | Fetch format | Fetch rendered HTML from the allowlisted domain and parse the `<main>` content region | Keeps canonical URLs faithful |
| D4 | LLM/embedding providers | **LLM:** Groq `openai/gpt-oss-120b` through the OpenAI-compatible chat-completions API (`https://api.groq.com/openai/v1`), using strict `json_schema` structured output. **Embeddings:** Gemini `gemini-embedding-001` through the native `batchEmbedContents` API (`https://generativelanguage.googleapis.com/v1beta`), with task types `RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`. Both sit behind protocols, so either can be swapped | Groq has no embedding models; Gemini's native API exposes `outputDimensionality` and task types, which its OpenAI-compatible endpoint does not |
| D5 | Embedding dimension | **1536**, via `outputDimensionality`; the `vector(1536)` column is created from config; vectors are L2-normalised client-side | Gemini's default of 3072 exceeds the HNSW limit of 2000; `gemini-embedding-001` does not normalise truncated outputs |
| D6 | Vector index | HNSW with `vector_cosine_ops` | Good recall without training step |
| D7 | Lexical index | Generated `tsvector` column + GIN index; query with `websearch_to_tsquery`; second exact-phrase pass for quoted error strings | Exact error strings and commands matter **[§12.3]** |
| D8 | Threshold semantics | Apply the relevance threshold on a **comparable score**: rerank score if reranker enabled, else max cosine similarity of the top candidates; RRF scores are rank-based and not comparable across queries | RRF values are tiny and query-dependent; thresholding them is meaningless |
| D9 | Background jobs | FastAPI background task + DB row status + Postgres advisory lock for single-flight ingestion. No Celery/Redis | Spec forbids unjustified infra **[§6.4]** |
| D10 | Admin protection | `ADMIN_MODE` = `token` \| `local` \| `dev`; `token` requires `X-Admin-Token` matching an env secret | Satisfies **[§41]** with minimal machinery |
| D11 | Grounding depth | Deterministic checks (mandatory) + claim-level verification via a second LLM call (stretch, flagged in config) | Spec lists deterministic checks as minimum, claim-level as stronger **[§20]** |
| D12 | Rate limiting | In-process token bucket keyed by client IP + route class | No Redis available by design **[§79]** |
| D13 | Tokenizer | `tiktoken` `o200k_base` (gpt-oss family), baked into the image | Accurate budgets for the LLM **[§99]**; chunks stay far below Gemini's 2048-token input limit |
| D14 | Integration-test DB | Real Postgres+pgvector via Docker (the Compose Postgres's `<db>_test` database) | pgvector behavior cannot be faked |

---

## Phase 0: Prerequisites and Decisions (done)

1. ~~Obtain API keys~~ Groq key (LLM) and Gemini key (embeddings) are in `.env`, and both answer a read-only model listing. `openai/gpt-oss-120b` is available on Groq; `gemini-embedding-001` on Gemini.
2. ~~Read the GitLab docs licence/terms and robots.txt~~ Done; see D2.
3. ~~Fill in D1-D14~~ Done.
4. ~~Create the repository, `.gitignore`, `README.md` with non-affiliation statement~~ Done in Phase 1.

---

## Phase 1: Foundation **[§5, §6, §7, §42-44]** (done)

Repository skeleton, typed config, Docker Compose, Postgres+pgvector, Alembic baseline (`0001`–`0003`), backend and frontend skeletons, error contract, request IDs, structured logging, admin guard. See `docs/CHANGELOG.md`.

---

## Phase 2: Knowledge Ingestion **[§8, §9, §10, §70, §71]**

**Deliverables:** manifest, hardened fetcher, parser/cleaner, structure-aware chunker, embedding pipeline, versioned staged publish, ingestion job tracking.

### 2.1 Source manifest and licence
- `knowledge/manifest/sources.json`: entries with `document_id`, `title`, `canonical_url`, `category`, `source_type`, `license`, `source_version`, `enabled` **[§8.2]**. `retrieved_at` and `content_hash` are filled by the pipeline, never hand-written.
- Curate URLs per category (authentication, api, ci_cd, runners, projects, permissions, integrations, webhooks, troubleshooting, security, configuration).
- Manifest schema validated by Pydantic; loader rejects duplicates, non-allowlisted domains, and non-HTTPS URLs.
- Write `docs/licensing.md` and the attribution string exposed by the API **[§51]**.

### 2.2 Fetcher (`DocumentFetcher` protocol) **[§9.2, §40, §78]**
- Domain allowlist from `ALLOWED_SOURCE_DOMAINS`; robots.txt enforced.
- URL validation, then **DNS resolution check**: reject loopback, private, link-local, and metadata addresses (IPv4 and IPv6); the connection is pinned to the validated IP (TLS still verified against the hostname), and every redirect hop is re-validated.
- Manual redirect following with max hops; connect/read timeouts; response-size cap enforced while streaming; content-type allowlist (`text/html`, `text/markdown`); exponential backoff with jitter and a bounded retry count; per-host rate limit; descriptive typed failures.
- Store raw body under `knowledge/raw/<document_id>/<hash>.html` (git-ignored).

### 2.3 Parser and cleaner (`DocumentParser` protocol) **[§9.3]**
- Extract main content region; drop nav, cookie banners, scripts, styles, footers, ads, duplicated headers, tracking query params.
- Output an intermediate **structured document tree**: title, heading hierarchy, paragraphs, lists, tables (serialized to readable text/markdown), code blocks (with language), meaningful links.
- Golden-file tests: saved real HTML fixtures → expected tree.

### 2.4 Chunker **[§9.4]**
- Walk the heading tree; pack sibling blocks into chunks up to `CHUNK_TARGET_TOKENS` with `CHUNK_OVERLAP_TOKENS` overlap (overlap only across paragraph boundaries).
- **Never split a code block or a numbered procedure** unless it alone exceeds the hard max (then split at line/step boundaries and mark the chunk).
- Prefix each chunk's embedded text with its heading path (title > section > subsection) so context is embedded, while `content` stores the readable text.
- Emit every metadata field in **[§9.4]** (`chunk_id` deterministic from `document_id + version + index + content_hash`).

### 2.5 Embedding pipeline (`EmbeddingProvider` protocol) **[§45]**
- `GeminiEmbeddingProvider`: native `batchEmbedContents` calls, at most 100 inputs per request (`EMBEDDING_BATCH_SIZE`); `taskType=RETRIEVAL_DOCUMENT` with the document title for chunks and `RETRIEVAL_QUERY` for queries; `outputDimensionality=EMBEDDING_DIMENSION` (top-level request fields; batch requests ignore `embedContentConfig` and reject `autoTruncate`, so the chunk hard max keeps inputs under Gemini's 2048-token limit).
- Bounded concurrency, client-side request pacing (`EMBEDDING_MAX_REQUESTS_PER_MINUTE`), retry with backoff on 429/5xx (honouring `Retry-After`), timeout, typed `EmbeddingProviderError`.
- L2-normalise returned vectors; validate returned count and dimension.
- **Chunk-level reuse**: if a new version contains a chunk whose `content_hash` + embedding spec (`model@dimension`) already exists, copy its embedding instead of re-calling the provider **[§3.1 item 27]**.

### 2.6 Versioning and staged publish **[§10]**
- `documents.status`: `staged` → `active` → `superseded` (plus `failed`).
- Flow per document: compare fetched content hash to the active hash. Equal (and same pipeline signature) → skip. Different → create version N+1 as `staged`, parse, chunk, embed, persist.
- **Publish step** (after validation of the whole job): in one transaction, flip staged → active and previous active → superseded. Retrieval only ever queries `active` + `enabled`. A failed job leaves the old KB untouched.
- Validation before publish **[§70]**: non-empty content, chunk count > 0, all embeddings present with correct dimension, no orphan chunks, hash recorded.

### 2.7 Ingestion jobs **[§11.3]**
- `IngestionService.run(config)`: creates a job row (`queued` → `running` → `validating` → `indexing` → `publishing` → `completed` | `failed` | `cancelled`), updates counters.
- Single-flight via Postgres advisory lock; second trigger returns `INGESTION_ALREADY_RUNNING` **[§94, §95]**.
- Per-document failures are recorded and do not abort the whole job unless configured.
- CLI: `python -m app.scripts.ingest [--force] [--only <document_id>]`.

**Tests**
- Unit: manifest validation, URL/SSRF validator (table-driven), chunker properties, hash-skip logic, Gemini provider against a mocked transport.
- Integration (real DB): full ingest with a **fake fetcher and fake embedder** (test-only doubles), version bump on changed content, failed job leaves active KB intact, chunk reuse avoids provider calls, single-flight lock.
- One **manual smoke run** against the real source and real Gemini embeddings, results noted in `docs/ingestion.md`.

**Exit gate:** real ingest completes; DB shows documents, versions, chunks with embeddings; second run reports 0 changed documents and makes 0 embedding calls; changed-document scenario passes.

---

## Phase 3: Retrieval **[§12, §13, §59, §81, §96-99]**

**Deliverables:** independent `RetrievalService` with query transformation, vector search, lexical search, RRF fusion, optional reranker, threshold, dedupe, diagnostics.

1. **Query transformation**: normalize whitespace/Unicode; extract technical terms with deterministic regex (error strings, `CLI --flags`, config keys, `CI_*` variables, versions, HTTP codes, file names); build `retrieval_query` = title + salient description lines + extracted terms. Preserve identifiers verbatim; enforce max query length **[§12.1, §40]**. LLM rewrite optional, off by default.
2. **Semantic search**: embed the query with `RETRIEVAL_QUERY`, `ORDER BY embedding <=> :q LIMIT candidate_k`, filtered to active+enabled docs; `semantic_score = 1 - cosine_distance`.
3. **Lexical search**: `ts_rank_cd` over `search_vector`, `websearch_to_tsquery`; extra exact-phrase query for quoted or extracted error strings; `lexical_score`.
4. **Fusion**: Reciprocal Rank Fusion, `k` and per-list weights from settings; deterministic tie-break (higher semantic score, then `chunk_id`). Expose `method` (`vector` | `lexical` | `hybrid`) **[§12.4]**.
5. **Reranker**: `Reranker` protocol **[§12.5]**; `NoopReranker` default; one concrete implementation (LLM-based via gpt-oss-120b) behind `RERANKER_ENABLED`. System must work without it.
6. **Threshold** per D8: compute `final_score`, filter, report `evidence_sufficient` with reason **[§12.6]**.
7. **Dedupe and diversity** **[§59, §98]**: drop near-duplicate chunks, cap chunks per document, prefer multi-document coverage.
8. **Response contract** **[§13]**: stable per-request `source_id`s (`SRC-001...`), all four scores plus `final_score`. Never leak internals.
9. **Endpoints**: `POST /search`, `POST /tickets/retrieve`, `GET /documents/{id}/chunks` (paging).

**Tests:** RRF math, tie-breaking, threshold, dedupe, term extraction; integration with known deterministic vectors (vector-only, lexical-only, hybrid; exact-error-string query found lexically); golden queries against the real ingested corpus.

**Exit gate:** `POST /search` returns real ranked evidence with scores for the three methods; weak queries report insufficient evidence.

---

## Phase 4: AI Resolution **[§14-21, §47, §58, §60, §77, §100-105]**

1. **LLM provider** (`LLMProvider` protocol **[§16]**): OpenAI-compatible client pointed at Groq, model `openai/gpt-oss-120b`, using `response_format: json_schema` with `strict: true`; `reasoning_effort` from config; per-call timeout, max retries with backoff (honouring Groq 429 `retry-after`), token limits, temperature and seed from config; typed `LLMProviderError` / `LLMTimeoutError`. Reasoning text returned by the provider is never logged or shown.
2. **Prompt store** **[§17]**: versioned text files (`classification_v1.txt`, `ticket_analysis_v1.txt`, `grounding_v1.txt`), `PromptRegistry` reporting `prompt_version`. System prompt contains all ten rules in **[§17]**.
3. **Classification** **[§14]**: centralized category enum injected into prompt and schema; unknown values rejected; confidence; ambiguity flag.
4. **Priority** **[§15]**: written rubric (`docs/priority-rubric.md`) covering the eight factors; LLM extracts **signals**, a **deterministic rubric function** maps them to `low|medium|high|critical`. UI labels this an AI-assisted estimate.
5. **Evidence context builder** **[§58, §99]**: delimiters, source IDs, token budget (drop lowest-ranked first), neutralize delimiter-like sequences, label content as untrusted data.
6. **Structured generation** **[§18]**: schema as the spec's example; one bounded correction attempt, then `INVALID_MODEL_OUTPUT`. Never loop.
7. **Citation validator** **[§19]**: `SRC-\d{3}` format, must exist in the evidence set; backend resolves IDs to title/section/URL. The LLM never supplies URLs.
8. **Grounding validator** **[§20]**: deterministic checks 1-6 plus **[§101, §102]** (commands/flags/config keys must appear in cited evidence; version claims must match). Optional claim-level pass (D11).
9. **Human-review decision** **[§21]**: pure function returning `required` + machine-readable `reasons`. Output always labelled **AI-generated draft**.
10. **Unsupported / insufficient evidence** **[§60]**: fixed safe message, `grounded=false`, `needs_human_review=true`.
11. **Orchestrator** (`TicketAnalysisService`) with per-stage timings **[§28]**.
12. **Endpoints**: `POST /tickets/analyze`, `POST /tickets/classify`.

**Tests:** `FakeLLMProvider` scenarios per [§47] (valid, correction, double failure, hallucinated citation, out-of-set citation, unsupported certainty, timeout, provider error); rubric, review-decision matrix, grounding checks; prompt-injection tests **[§61, §77]**; integration + E2E with fakes; one manual real-LLM run on ~10 tickets recorded in `docs/evaluation.md`.

**Exit gate:** analyze endpoint returns a valid response for supported tickets and a safe insufficient-evidence draft for unsupported ones; every failure scenario has a passing test.

---

## Phase 5: API Completion and Contracts **[§22-25, §52, §53, §90-95]**

1. Remaining routes: `GET /ready`, `GET /documents`, `GET /documents/{document_id}`, `POST /ingestion/run`, `GET /ingestion/status`, `GET /ingestion/{job_id}`, and evaluation routes (`POST /evaluations/run` lands with Phase 7, never as a stub).
2. Request/response Pydantic models for every route with length/size/type validation **[§23]**.
3. Consistent pagination and error responses; correct status mapping; no stack traces.
4. Server-side timeouts around external calls **[§92]**; idempotency and concurrency rules **[§94, §95]**.
5. Accurate OpenAPI; snapshot in `docs/api.md`.
6. API tests for every route: happy path, validation error, not found, unauthorized (admin), dependency-down.

**Exit gate:** every documented route exists and passes tests (evaluation routes gated to Phase 7).

---

## Phase 6: Frontend **[§26-35, §64-67, §83-87]**

1. Foundations: typed API client (done in Phase 1), TanStack Query conventions, shared components (`PageHeader`, `MetricCard`, `StatusBadge`, `DataTable`, `EmptyState`, `ErrorState`, `LoadingState`, `ScoreBar`, `SourceCard`, `ReviewBanner`).
2. Ticket Analysis page **[§28]** with persistent **AI-generated draft, not human-approved** notice.
3. Retrieval Explorer **[§29]**. 4. Knowledge Base **[§30, §85]**. 5. Ingestion **[§31]**. 6. Evaluation **[§32]** (wired in Phase 7). 7. System Status **[§33]**. 8. Dashboard **[§27]** (real aggregates only).
9. All UI states **[§34]**. 10. Accessibility and responsiveness **[§35, §65]**.

**Tests:** component tests for state variants; Playwright happy path against the compose stack in Phase 9.

**Exit gate:** every control triggers a real backend call; killing the backend produces clear error states.

---

## Phase 7: Evaluation **[§36-38, §62, §63, §86-88]**

1. Dataset `evaluation/cases.json` (40-60 cases covering the 17 types in **[§36]**); a test fails if expected URLs are missing from the KB.
2. Runner **[§37]**: CLI and `POST /evaluations/run` (admin-protected, single-flight, background).
3. Metrics with formulas in `docs/evaluation.md` **[§62]**; deterministic vs LLM-judged metrics labelled separately.
4. Experiment configuration **[§38]**; preset experiments **[§63]** (embedding-model experiments need a separate index or re-ingest).
5. UI: Evaluation page and dashboard panels with real stored results.
6. Reproducibility **[§88]**: temperature 0 and seed on Groq; config and prompt versions stored; residual non-determinism documented (gpt-oss reasoning output can still vary).

**Exit gate:** a full real evaluation run completes and renders; hybrid vs vector-only comparison visible.

---

## Phase 8: Security and Observability Hardening **[§39, §40, §41, §48, §61, §77-80, §106-107]**

Admin protection tests; input limits; SSRF suite; prompt-injection defenses end-to-end; rate limiting and cost control (including provider free-tier limits); observability (request IDs, safe structured logs, `request_logs`, LLM payload logging off by default, redaction); privacy (no ticket bodies persisted by default); failure-handling matrix; supply chain (`pip-audit`, `npm audit`, **Python lockfile**); containers.

**Exit gate:** `tests/security` passes; logs contain no secrets or LLM payloads.

---

## Phase 9: Testing, Documentation, Release **[§46, §50-52, §108, §111-117]**

Test completion including one true E2E; full documentation set; final quality audit; clean-room deployment test; acceptance checklist with evidence.

**Exit gate:** clean-room run passes end to end; audit has no unexplained hits; documentation complete.

---

## 2. Cross-Cutting Concerns

### 2.1 Contracts first
Shared Pydantic models: `TicketInput`, `NormalizedTicket`, `Classification`, `PriorityAssessment`, `RetrievedChunk`, `RetrievalResult`, `GeneratedResolution`, `GroundingReport`, `HumanReviewDecision`, `TicketAnalysisResponse`, `ErrorResponse`, `IngestionJobStatus`, `EvaluationRunConfig`, `EvaluationMetrics`. Frontend Zod schemas mirror them.

### 2.2 Test doubles policy
Fakes (`FakeLLMProvider`, `FakeEmbeddingProvider`, `FakeFetcher`) live only in `backend/tests/`. Production wiring uses real providers selected by config.

### 2.3 Error taxonomy mapping **[§25, §76]**

| Situation | Code | Behavior |
|---|---|---|
| Bad input | `INVALID_REQUEST` | 422/400 |
| Unknown document | `DOCUMENT_NOT_FOUND` | 404 |
| Second ingestion run | `INGESTION_ALREADY_RUNNING` | 409 |
| DB/vector failure | `RETRIEVAL_UNAVAILABLE` | 503 |
| Embedding failure (Gemini) | `EMBEDDING_PROVIDER_ERROR` | 502/503 |
| LLM failure/timeout (Groq) | `LLM_PROVIDER_ERROR` / `LLM_TIMEOUT` | 502/504, or graceful review draft for analyze |
| Bad model output after retry | `INVALID_MODEL_OUTPUT` | review draft with reason |
| Weak evidence | `INSUFFICIENT_EVIDENCE` | 200 with safe draft, `grounded=false` |
| Grounding/citation failure | `GROUNDING_FAILED` / `CITATION_VALIDATION_FAILED` | 200 with safe draft and review required |
| Anything else | `INTERNAL_ERROR` | 500, no stack trace |

---

## 3. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Source licence/robots disallow intended use | Blocks ingestion | Verified (D2); fetcher enforces robots.txt; fetcher is source-agnostic |
| Real HTML is messy | Poor retrieval | Golden fixtures from real pages; iterate the parser on varied pages before scaling |
| Embedding model/dimension change late | Re-migration and re-embed | Fixed at 1536 (D5); embedding spec stored per chunk; re-embed procedure documented |
| **Gemini / Groq free-tier rate limits** | Slow or failed ingestion and evaluation | Client-side pacing, `Retry-After`-aware backoff, chunk-level embedding reuse, small curated manifest, bounded evaluation size |
| **Two providers, two failure domains** | Partial outages | Distinct error codes per provider; `/ready` and System Status report each separately |
| **gpt-oss reasoning variance** | Less reproducible outputs | Temperature 0, seed, low/medium reasoning effort, strict JSON schema, bounded correction retry; residual variance documented |
| Threshold mis-set | Quality failure | D8; tune on evaluation set; report correct and false refusal rates |
| Evaluation cases drift from ingested docs | Invalid metrics | Test that every expected URL exists in the KB |
| Scope creep in frontend | Schedule slip | Shared components first; pages in spec order |
| Claiming things work without proof | Spec violation | Acceptance checklist requires evidence per item |

---

## 4. Definition of Done (Condensed from [§111])

All items verified on 2026-09-30; evidence in `docs/acceptance.md`.

- [x] `docker compose up --build` works from a clean clone; migrations and ingestion documented and working
- [x] Real documentation ingested with preserved metadata, versioning, and staged publish
- [x] Vector, lexical, hybrid retrieval (and optional reranker) work and are inspectable
- [x] Classification, priority (documented rubric), structured generation, citation validation, grounding validation, and human-review logic work
- [x] Unsupported and low-evidence tickets get a safe, honest response
- [x] All 15 API routes implemented, validated, and documented; OpenAPI accurate
- [x] All 7 frontend pages use real APIs; all async states handled; no fake numbers anywhere
- [x] Evaluation runner, documented metrics, and stored reproducible experiments visible in the UI
- [x] Request IDs, structured logs, no secrets or LLM payloads logged
- [x] SSRF, prompt injection, input limits, admin protection, safe errors verified by tests
- [x] Unit, integration, API, retrieval, security, and E2E tests pass
- [x] README, architecture, ingestion, retrieval, evaluation, security, licensing, and API docs complete
- [x] Final quality audit clean; clean-room acceptance scenario passes
- [x] No required feature is a mock, placeholder, or TODO
