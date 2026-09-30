# Security and observability

This platform handles untrusted input from three directions: customers (ticket text),
the public web (documentation pages), and language models (generated output). This
document lists each defence, where it lives, and the tests that prove it. The security
suite is `backend/tests/security` (`make test-security` / `./tasks.ps1 test-security`).

## Threat model in one table

| Threat | Defence | Code | Tests |
|---|---|---|---|
| SSRF through ingestion (private IPs, DNS rebinding, redirects to internal hosts) | Allow-listed domains (`ALLOWED_SOURCE_DOMAINS`), HTTPS only, every resolved address must be public, the connection is pinned to the validated IP with the original Host header and SNI, each redirect hop is re-validated, bounded redirects | `app/services/ingestion/url_safety.py`, `fetcher.py` | `test_ssrf_url_safety.py`, `test_ssrf_fetcher.py` |
| Resource exhaustion through ingestion | Streamed body cap, declared-length check, content-type allow-list, bounded retries, per-host pacing, robots.txt honoured (failure to read it disallows) | `fetcher.py` | `test_ssrf_fetcher.py` |
| Prompt injection in tickets | Detected and flagged (`prompt_injection_suspected` review reason); ticket wrapped in delimiters; delimiter look-alikes neutralised; system prompt declares ticket and sources untrusted | `app/services/analysis/context.py`, `prompts/system_v1.txt` | `test_prompt_injection.py` |
| Prompt injection in retrieved documentation | Suspicious chunks are excluded from the model's context and reported (`retrieval.excluded_for_injection`) | `context.py` | `test_prompt_injection.py` |
| A hijacked or hallucinating model | Output must match a strict JSON schema; every step must cite a source that was actually provided; commands, paths, and config keys must appear in the cited text (or the ticket); failing drafts are withheld, never shown | `validation.py`, `service.py` | `test_prompt_injection.py`, `tests/unit/test_analysis_rules.py` |
| Unauthorised operations | Ingestion and evaluation runs need `X-Admin-Token` (constant-time compare) in `ADMIN_MODE=token`; `local` allows loopback peers only and ignores forwarded headers; `dev` is refused when `ENVIRONMENT=production` | `app/core/security.py` | `test_admin_protection.py` |
| Abuse and cost blow-ups | Per-client sliding-window limits on analysis (`RATE_LIMIT_ANALYZE_PER_MINUTE`, 20) and search (`RATE_LIMIT_SEARCH_PER_MINUTE`, 60); 429 with `Retry-After`; X-Forwarded-For is trusted only from `TRUSTED_PROXY_CIDRS` (Compose: the frontend's fixed address). Provider-side: client pacing to Groq's tokens/minute and NVIDIA's requests/minute; daily-quota errors fail fast; embedding circuit breaker | `app/core/rate_limit.py`, `app/providers/*` | `test_rate_limit.py`, `tests/unit/test_llm_provider.py` |
| Oversized or malformed input | Body limit `MAX_REQUEST_BODY_BYTES` (256 kB) enforced on declared and streamed bodies; ticket title ≤ 300 and description ≤ 20,000 characters; Pydantic validation on every route | `app/core/middleware.py`, `app/schemas/*` | `test_safe_errors.py` |
| Information leakage in errors | One error contract; unhandled errors return an opaque 500; validation errors never echo submitted values | `app/core/exception_handlers.py` | `test_safe_errors.py` |
| Secrets or payloads in logs | JSON logs with redaction of sensitive keys and of known secret formats (Bearer tokens, `sk-`, `gsk_`, `nvapi-`, `hf_`, `AIza`, `AQ.`, GitLab `glpat-`/`glrt-`, database URL passwords); provider errors logged as metadata only; LLM payload logging off by default (`LOG_LLM_PAYLOADS=false`); settings hold keys as `SecretStr` | `app/core/logging.py`, `app/providers/llm.py` | `test_log_safety.py` |

## Observability

- **Request IDs.** Every response carries `X-Request-ID` (a safe client-supplied value is
  honoured, otherwise a UUID). The ID is attached to every log line and error body.
- **Structured logs.** One JSON object per line on stdout (scripts log to stderr so their
  stdout stays machine-readable). One `request completed` line per request.
- **`request_logs` table.** One row per API request with the route template (never the
  concrete path), status, latency, and error code; for ticket analysis also stage timings
  and an outcome summary (status, category, priority, grounding, review reasons, source
  count). Ticket text, retrieved content, and model output are never stored. Health and
  readiness probes are not recorded. Writes happen off the request path in batches; if the
  database is down the request still succeeds. Rows older than
  `REQUEST_LOG_RETENTION_DAYS` (30) are pruned at startup; `REQUEST_LOG_ENABLED=false`
  turns it off.
- **Readiness.** `GET /api/v1/ready` reports the database, pgvector, knowledge base, and the
  last failure of each provider without spending provider quota.

## Privacy

Tickets are processed in memory and not persisted. The only stored traces of a ticket
are the outcome metadata above. Evaluation cases are authored data in
`evaluation/cases.json`, not customer tickets.

## Supply chain and containers

- Python dependencies are pinned with hashes in `backend/requirements.lock` (runtime) and
  `requirements-dev.lock` (tooling); the image installs with `pip --require-hashes`.
  Regenerate with `make lock` after editing `pyproject.toml`. npm uses `package-lock.json`
  with `npm ci`.
- `make audit` runs `pip-audit` over the locked set and `npm audit`. Last run (2026-09-30):
  no known vulnerabilities in either.
- Containers run as non-root (backend uid 10001, nginx-unprivileged uid 101), with
  read-only root filesystems (backend and frontend), `tmpfs` for `/tmp`, all Linux
  capabilities dropped, and `no-new-privileges`. Every published port binds to
  127.0.0.1. The tokenizer encoding is baked into the image so nothing is downloaded at
  runtime.

## Secrets

Secrets live only in `.env` (git-ignored). `.env.example` documents every variable with
empty values. The API never returns configuration values; `/version` and `/ready` expose
model names and provider status, not keys. Commits in this repository were checked for
the literal values of every key in `.env` before each commit.

## Known limits

- Rate limits are in-process: they reset on restart and would need a shared store if the
  backend were scaled to several replicas (ADR D1 keeps one).
- Injection detection is pattern-based. It is a signal for human review, not the main
  defence; the main defence is that nothing the model writes reaches the user unless it
  validates against the cited evidence.
- The admin token is a single shared secret. There are no user accounts; the platform is
  designed for a support team's internal network.
