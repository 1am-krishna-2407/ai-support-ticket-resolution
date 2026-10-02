# Changelog

## UI: review banner removed (2026-10-02)

The amber "AI-generated draft / human review required" banner above every analysis is
gone. Users of the tool know drafts are AI-generated, and routine reasons (priority,
follow-up questions, insufficient evidence) are already visible on the page. Serious
reasons only (security concern, data loss, prompt injection, failed grounding or
citations, unsupported identifiers) appear as a one-line "Check before sending" row at the
bottom of the Resolution panel. The API still returns `label` and `human_review`.

## Phase 9: Testing, documentation, release (`phase-9-complete`, 2026-09-30)

**Added** `backend/tests/e2e` (11 tests against the live stack through nginx, including an
opt-in real ticket analysis); `docs/acceptance.md` with evidence for every
definition-of-done item; rewritten README; final quality audit.

**Verified** clean-room run: fresh clone, `--no-cache` build (hash-checked Python install,
`npm ci`), migrations 0001–0005 on an empty database, real ingestion of three pages
(73 chunks embedded; re-run makes 0 embedding calls), and the E2E suite passing. Stopping
the backend shows a clear "Service unavailable" state in the UI, which recovers on
restart.

**Fixed** the ingestion script logged to stdout, mixing log lines into its JSON summary;
logs now go to stderr like the other scripts.

## Phase 8: Security and observability hardening (`phase-8-complete`, 2026-09-30)

**Added** per-client sliding-window rate limits for analysis (20/min) and search (60/min)
with `429 RATE_LIMITED` and `Retry-After`; `X-Forwarded-For` is honoured only from
`TRUSTED_PROXY_CIDRS` (Compose pins the frontend proxy to 172.28.10.10). `request_logs`
now receives one metadata-only row per API request (route template, status, latency,
error code, analysis timings and outcome), written off the request path, pruned after
`REQUEST_LOG_RETENTION_DAYS`; the unused `PERSIST_TICKET_BODIES` switch is gone because
ticket text is never stored. Log redaction now covers Groq, NVIDIA, Hugging Face, Google
and GitLab token formats. Hashed Python lockfiles (`requirements.lock`,
`requirements-dev.lock`) installed with `--require-hashes`; `make lock`, `make audit`.
Containers: read-only root filesystems, `tmpfs /tmp`, all capabilities dropped,
`no-new-privileges`. `docs/security.md`.

**Tests** `backend/tests/security`: admin protection, SSRF (URL rules and fetcher),
prompt injection end to end (detection, containment, hijacked output withheld), rate
limits, safe errors, log safety (no keys, ticket text or model output in logs, including
when a provider echoes secrets). `pip-audit` and `npm audit`: no known vulnerabilities.

## Phase 7: Evaluation (`phase-7-complete`, 2026-09-30)

**Added** 51-case dataset over 17 types (`evaluation/cases.json`, validated against the
knowledge base); runner with frozen configuration, single-flight lock, and provider
outages recorded as errors rather than scores; CLI (`app.scripts.evaluate`) and
admin-only `POST /evaluations/run`; deterministic retrieval and answer metrics with
formulas in `docs/evaluation.md`; Evaluation page with per-case results and run-to-run
comparison.

**Results** retrieval for hybrid, vector and lexical (51 cases each) and a full generation
run on Nemotron are recorded in `docs/evaluation.md`.

## Phase 6: Frontend (`phase-6-complete`, 2026-09-30)

**Added** seven pages on real APIs (Dashboard, Ticket Analysis, Retrieval Explorer,
Knowledge Base with document detail, Ingestion, Evaluation, System Status) with Zod-
validated responses, loading, empty, error, degraded and rate-limited states, request IDs
on every error, and a persistent *AI-generated draft* notice. Tables stay page-specific
(document list, evaluation cases, metric comparison) rather than a generic `DataTable`,
because each has different columns, links and states.

**Tests** 33 component tests (state variants per page), plus the end-to-end suite against
the live stack.

## Phase 5: API completion and contracts (`phase-5-complete`, 2026-09-30)

**Added** all routes (16 operations) with Pydantic request and response models, limits,
pagination, and the single error contract; every route documents its real error
responses in OpenAPI; `docs/openapi.json` and `docs/api.md` are generated
(`make openapi`) and a unit test fails if they drift. Server-side timeouts on every
provider call and a database statement timeout; single-flight ingestion and evaluation.

## LLM provider change (2026-09-30)

- LLM: NVIDIA `nvidia/nemotron-3-super-120b-a12b` via the NVIDIA API catalog
  (`LLM_PROVIDER=nvidia`, key in `NEMOTRON_API_KEY`). Groq `gpt-oss-120b` stays selectable
  with `LLM_PROVIDER=groq`. Base URL, model and pacing default per provider; Groq keeps
  tokens-per-minute pacing, NVIDIA gets requests-per-minute pacing. A per-day 429 (Groq's
  200,000 tokens/day) now fails fast. Grounding accepts identifiers that appear in the
  ticket itself (the customer's own hostname is not a product claim). ADR D4 revised.
- Reasoning allowance: `LLM_REASONING_TOKENS` (NVIDIA 4,000, Groq 0) is added to every
  request's output limit, and the NVIDIA timeout is 180 s. The grounding report now names the
  model's own doubt (`model_self_assessed_grounded`) when that is why a draft is withheld.

## Provider change (2026-09-29)

- LLM: Groq `openai/gpt-oss-120b` (OpenAI-compatible, strict JSON-schema output). Embeddings:
  Gemini `gemini-embedding-001` at 1536 dimensions (native `batchEmbedContents`). Groq has
  no embedding models. ADRs D1, D2, D4, D5, D13 revised; plan saved to
  `docs/implementation-plan.md`.

## Phase 4: AI resolution (`phase-4-complete`, 2026-09-30)

**Added** Groq chat provider (retries, timeouts, truncation detection, reasoning text never
returned); versioned prompts (`system_v1` with ten rules, `classification_v1`,
`ticket_analysis_v1`, `grounding_v1`); triage (classification + eight priority signals in
one call); deterministic priority rubric; delimited, budgeted evidence context with
injection screening; structured generation with one bounded correction attempt; citation
validator; grounding validator (evidence, threshold, citations, cited steps, identifiers
present in cited text, version claims, confidence calibration, grounded-flag consistency,
optional claim-level check); human-review decision with 15 reasons; `TicketAnalysisService`;
`POST /tickets/analyze`, `POST /tickets/classify`.

**Verified** with scripted providers: valid draft, correction, double failure, hallucinated
and out-of-set citations, invented commands, overconfidence, timeouts, provider errors,
retrieval failure, insufficient evidence (no generation call), injection in the ticket and
in retrieved chunks. One real Groq call confirmed strict-schema triage output (1.8 s).

**Real run** (docs/evaluation.md): 10 tickets on Nemotron, 7 grounded drafts, 1 withheld for an
identifier missing from its cited sources, 2 insufficient-evidence (one out of scope). Every
status shown to the user comes with its reason.

## Phase 3: Retrieval (`phase-3-complete`, 2026-09-30)

**Added** query transformation (verbatim extraction of CI variables, flags, versions, HTTP
codes, file names, config keys, commands, error lines, quoted strings); vector search
(HNSW, iterative scan, active + enabled filter); OR-lexeme full-text search; exact-phrase
search; RRF fusion with deterministic tie-breaks; dedupe and a per-document cap; reranker
protocol (no-op default); threshold on cosine similarity with an exact-phrase override (D8);
`POST /search`, `POST /tickets/retrieve`, `GET /documents/{id}/chunks`.

**Verified** on real pgvector with a known-vector corpus: rankings for each method,
inactive/disabled exclusion, the exact error string found when semantically distant, weak
queries flagged insufficient, cap and dedupe, category filter, determinism, API validation.

**Open:** golden queries and threshold tuning on the real corpus need query embeddings.

## Phase 2: Knowledge ingestion (tag `phase-2-complete`)

**Added** 217-page manifest (curation script + priority lists); SSRF-safe fetcher (pinned IP
with SNI, per-hop re-validation, robots.txt, size cap, pacing, retries); GitLab parser with
golden files; structure-aware chunker; Gemini embedding provider (task types,
normalisation, dimension checks, `RetryInfo`-aware backoff, fail-fast on daily quota);
versioned staged publish; single-flight advisory lock; interrupted-job recovery; reuse-only
mode after quota exhaustion; `python -m app.scripts.ingest`; migration `0004`;
`docs/licensing.md`, `docs/ingestion.md`.

**Verified:** 45 real documents (989 chunks) active; re-run detects them unchanged with zero
embedding calls; recovery and reuse proven on a real interrupted run.

**Exit gate passed (2026-09-30):** all 217 documents ingested with Qwen3-Embedding-0.6B (4,600
chunks, all embedded); an immediate re-run reported 217 unchanged and 0 embedding calls; a
real changed page produced a new version with reused embeddings. See docs/ingestion.md §9.

**Fixed along the way:** Alembic `fileConfig` disabled the app's loggers after in-process
migrations; cancelled jobs were finalised as `running` with a finish time.

## Phase 1: Foundation (tag `phase-1-complete`)

**Added**
- Repository layout, `.gitignore`, `.gitattributes`, `.env.example`, Makefile and `tasks.ps1` (Windows).
- Backend: typed settings with fail-fast validation, error taxonomy and standard error contract,
  request-ID middleware, structured JSON logging with redaction, request body size limit,
  admin guard (`token` / `local` / `dev`), `GET /api/v1/health`, `GET /api/v1/version`.
- Database: SQLAlchemy models for all six tables; Alembic migrations `0001` (pgvector),
  `0002` (core schema), `0003` (HNSW, generated tsvector + GIN, metadata and partial indexes).
- Docker: Compose stack (pgvector/pgvector:pg17, backend, frontend) with healthchecks; non-root
  backend (uid 10001) and nginx-unprivileged frontend (uid 101); ports bound to 127.0.0.1.
- Frontend: Vite + React 19 + TypeScript + Tailwind 4 + React Router + TanStack Query + Zod;
  app shell with the seven sections; typed API client (Zod-validated responses, timeouts,
  error-contract decoding); live API status indicator.

**Verified**
- Backend: 58 tests pass (51 unit, 7 integration on real Postgres + pgvector); ruff and mypy --strict clean.
- Frontend: 19 tests pass; tsc and eslint clean; production build succeeds.
- `docker compose up --build`: all three services healthy; `alembic upgrade head` creates all tables
  and 32 indexes; `pip-audit` and `npm audit` report no known vulnerabilities.
- With the backend stopped, the UI shows "API unreachable" (after the 5 s client timeout) instead of a blank page.

**Open items carried forward**
- Spec file (`ai_support_ticket_resolution_platform_spec.md`) not yet in the repository; Phase 1
  followed the implementation plan. Table columns should be re-checked against spec §11 when it's available.
- D1/D5 provisional: LLM/embedding provider, embedding model and dimension, and source licence
  verification are needed before Phase 2.
- Python dependencies are range-constrained in `pyproject.toml` but not hash-locked; add a lock
  (e.g. `uv lock` or `pip-compile`) in Phase 8 (supply chain). `frontend/package-lock.json` is committed.
