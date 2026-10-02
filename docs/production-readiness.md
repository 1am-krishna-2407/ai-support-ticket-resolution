# Production readiness audit

Audit of the Support Resolution Platform (commit `7d655b8`, 2026-09-30) against an
**ideal, deployment-ready** version of the idea. The build plan's own definition of done
is met in full (`docs/acceptance.md`). This document raises the bar to what a support
organisation would need before real customers' tickets flow through the system.

Legend: ✅ achieved · ⚠️ partial · ❌ missing.

## Verdict

- **The idea is proven.** Real documentation is ingested, retrieval is measured, and drafts
  are grounded, cited and validated. Nothing unsupported was published in 61 real
  analyses, out-of-scope tickets are refused, and prompt injection is contained.
- **Ready for an internal pilot** on one trusted machine or network, with engineers
  reviewing every draft, after fixing one bug (timeouts, G1 below).
- **Not ready for production** with real customer data. The missing pieces are:
  - user authentication and TLS;
  - production-grade LLM providers with data-processing terms;
  - PII handling in prompts;
  - backups, CI, and metrics and alerting;
  - a feedback loop for engineers.
- **The largest product gap is coverage and speed:** 60% of answerable tickets get a
  draft, and an analysis takes about a minute (p95 of 3 minutes).

Scorecard: **27 ✅ · 10 ⚠️ · 27 ❌** across 64 items.

## 1. Product and answer quality

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 1.1 | Drafts cite the documentation for every step | ✅ | Citation validator; every published draft has valid citations |
| 1.2 | Unsupported content is never shown | ✅ | 0 unsupported drafts published in 51 evaluation cases and 10 real tickets; failing drafts are withheld |
| 1.3 | Out-of-scope tickets get an honest refusal | ✅ | Correct refusal rate 1.0, including the near-miss c45 |
| 1.4 | Classification and priority good enough to route tickets | ✅ | Category 0.93; priority exact 0.80, within one level 0.96 |
| 1.5 | Most answerable tickets get a usable draft (target ≥ 80%) | ❌ | Grounded rate 0.60; false refusal 0.19 (`docs/evaluation.md`) |
| 1.6 | Answer correctness measured (human grading or LLM judge) | ❌ | Only structural metrics; `llm_judged` is empty; drafts not graded |
| 1.7 | Evaluated on real historical tickets | ❌ | 51 authored cases and 10 hand-written tickets |
| 1.8 | Engineers can approve, edit or reject drafts, and that feedback is stored | ❌ | No feedback endpoint or UI; drafts are view-only |
| 1.9 | Integrated with the ticketing system (Zendesk, Jira, GitLab issues) | ❌ | Tickets are pasted into the UI or sent to the API |
| 1.10 | Answers match the customer's product version | ⚠️ | Ticket environment (version, deployment) is captured and used as a priority signal; the knowledge base holds only the latest docs |
| 1.11 | Interactive latency (p95 < 30 s) | ❌ | Median 59 s, p95 192 s (free Nemotron with reasoning) |
| 1.12 | Long analyses run as jobs with progress, not one long HTTP request | ❌ | `POST /tickets/analyze` is synchronous |
| 1.13 | Clear AI-draft labelling and review reasons | ✅ | Persistent label; 16 review reasons shown |

## 2. Knowledge base

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 2.1 | Licensed source with attribution | ✅ | CC BY-SA 4.0 verified; attribution in API and UI |
| 2.2 | Versioned, staged publish, incremental re-ingest | ✅ | Re-run: 0 changed, 0 embedding calls; interrupted-job recovery |
| 2.3 | Scheduled refresh so answers track doc changes | ❌ | Ingestion is started by hand (CLI or admin API) |
| 2.4 | Coverage of the product's support surface | ⚠️ | 217 curated pages over 11 categories; full GitLab docs are several thousand pages |
| 2.5 | Staleness visible (age of each document, failed refreshes) | ⚠️ | Last ingestion job and per-document versions are visible; no staleness alert |

## 3. Retrieval

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 3.1 | Hybrid search with a tuned relevance threshold | ✅ | RRF fusion; threshold 0.45 tuned on golden queries |
| 3.2 | Retrieval quality measured per method | ✅ | Recall@5: vector 0.87, hybrid 0.77, lexical 0.72 (51 cases) |
| 3.3 | Reranker for precision | ⚠️ | Interface and settings exist (`NoopReranker`); no model plugged in |
| 3.4 | Known ranking weaknesses addressed | ⚠️ | Misses c02, c03 open; vector beats hybrid, and the lower lexical weight experiment has not been run |

## 4. Reliability and operations

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 4.1 | Health and readiness checks | ✅ | `/health`, `/ready`, Compose health checks |
| 4.2 | Graceful degradation when a provider fails | ✅ | Lexical-only search, review-only analysis, circuit breaker, daily-quota fail-fast |
| 4.3 | Timeouts consistent from browser to provider | ⚠️ | **G1 mitigated 2026-10-02:** UI and nginx now wait 300 s (were 120 s), but 6 of 48 evaluation analyses and 2 of 10 real tickets took longer (slowest 376 s) |
| 4.4 | Production LLM and embedding tiers with SLAs | ❌ | NVIDIA free endpoint (intermittent 503s), HF credits, Groq free tier (200k tokens/day) |
| 4.5 | Automatic failover between LLM providers | ❌ | Two providers supported; switching is a manual `LLM_PROVIDER` change |
| 4.6 | Database backups with a tested restore | ❌ | Named volume only; no `pg_dump` schedule or restore drill |
| 4.7 | Container CPU and memory limits | ❌ | None in `docker-compose.yml` |
| 4.8 | High availability or more than one replica | ❌ | Single backend by design (ADR D1); rate limits and jobs are in-process |
| 4.9 | Explicit, tested migrations | ✅ | Alembic 0001–0005, migration tests, never auto-run |
| 4.10 | Operations runbook (incident, key rotation, re-ingest, restore) | ⚠️ | Commands in README and Makefile; no runbook |

## 5. Security

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 5.1 | Every endpoint requires an authenticated user | ✅ | Added 2026-10-02: local accounts with Argon2id passwords, server-side sessions, agent/admin roles; every route except health, version, readiness and sign-in returns 401 when signed out (tested route by route). No SSO or MFA |
| 5.2 | TLS on every hop | ❌ | HTTP on 127.0.0.1; no TLS termination or HSTS |
| 5.3 | SSRF protection for ingestion | ✅ | Allowlist, public-IP pinning, redirect re-validation; tests |
| 5.4 | Prompt-injection containment | ✅ | Detection, exclusion, delimiters, output validation; tests |
| 5.5 | Input limits and rate limits | ✅ | Body 256 kB, field limits; 20 analyses and 60 searches per minute per client, verified through nginx |
| 5.6 | Safe errors (no stack traces, no echoed input) | ✅ | Error contract; tests |
| 5.7 | Browser security headers | ✅ | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy` |
| 5.8 | Secrets in a secret manager, with rotation | ⚠️ | `.env` (git-ignored), `SecretStr`, never logged; no vault or rotation |
| 5.9 | Pinned, audited dependencies | ✅ | Hashed lockfiles, `--require-hashes`; `pip-audit`/`npm audit` clean |
| 5.10 | Automated dependency updates and image scanning | ❌ | No Dependabot or Renovate; no Trivy or Grype |
| 5.11 | Hardened containers | ✅ | Non-root, read-only filesystem, capabilities dropped, `no-new-privileges` |
| 5.12 | Audit trail of who ran what | ✅ | `request_logs.user_id` on every signed-in request; saved analyses record their owner |

## 6. Privacy and compliance

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 6.1 | Ticket data stored deliberately, access-controlled, with retention | ✅ | Analyses are saved per user (owner and admins only, users can delete), optional `TICKET_HISTORY_RETENTION_DAYS`; request logs stay metadata-only |
| 6.2 | Personal data and secrets removed before text goes to third-party models | ❌ | Redaction applies to logs only; prompts carry the ticket verbatim (a pasted token reaches the LLM provider) |
| 6.3 | Providers under data-processing terms fit for customer data | ❌ | Free or trial tiers; terms not reviewed for production data |
| 6.4 | Retention policy for stored operational data | ✅ | `request_logs` pruned after 30 days |

## 7. Observability

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 7.1 | Structured logs with request IDs | ✅ | JSON logs, `X-Request-ID` everywhere |
| 7.2 | Per-request operational record | ✅ | `request_logs` with timings and outcome |
| 7.3 | Metrics endpoint and dashboards (latency, error rate, drafts published or withheld) | ❌ | No Prometheus or OpenTelemetry; the UI dashboard shows KB and evaluation figures only |
| 7.4 | Alerting (provider outage, quota exhaustion, error spikes) | ❌ | Failures are visible on `/ready`; nobody is notified |
| 7.5 | Token and cost tracking per request | ❌ | Token counts are returned by providers but not stored |
| 7.6 | Distributed tracing | ❌ | Stage timings only |

## 8. Engineering process

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 8.1 | Unit, integration, security and E2E tests | ✅ | 473 backend, 11 E2E, 33 frontend |
| 8.2 | CI runs lint, tests and audits on every change | ❌ | No CI configuration; no git remote |
| 8.3 | Releases pushed to a remote with tags | ⚠️ | Tags `phase-1…9-complete` exist locally only |
| 8.4 | Browser-driven E2E of the UI | ⚠️ | HTTP-level E2E through nginx plus component tests and a manual browser check |
| 8.5 | Accessibility tested (axe, keyboard, screen reader) | ❌ | Semantic markup and ARIA roles, but no automated accessibility checks |
| 8.6 | Load test at expected ticket volume | ❌ | None |
| 8.7 | Clean-room reproducible build | ✅ | Fresh clone, `--no-cache`, empty database: passed |

## 9. Documentation

| # | Ideal | Status | Evidence |
|---|---|---|---|
| 9.1 | Architecture, ingestion, retrieval, evaluation, security, licensing and API docs | ✅ | `docs/` (generated API reference with drift test) |
| 9.2 | Honest results and known limits | ✅ | `docs/evaluation.md`, `docs/acceptance.md`, this audit |
| 9.3 | User guide for support engineers (how to read drafts, what review reasons mean) | ⚠️ | Review reasons are labelled in the UI; no written guide |

## Gap analysis and priorities

### P0: blockers for any use beyond a trusted pilot

| Gap | Why it matters | Effort |
|---|---|---|
| **G1. Timeout mismatch** (4.3) | 12% of evaluation analyses (and 2 of 10 real tickets) exceed 120 s. The UI shows a timeout and nginx returns 504 while the backend is still working and spending provider calls | Small: raise the nginx and UI limits above the backend's worst case, or make analysis a job (see G6) |
| **G2. TLS (authentication done 2026-10-02)** (5.2) | Accounts, roles and the audit trail exist; session cookies still travel over plain HTTP outside localhost | Small: TLS termination (for example Caddy) and `SESSION_COOKIE_SECURE=true`; SSO/MFA optional later |
| **G3. Production providers and data terms** (4.4, 6.3) | Free tiers are slow, rate-limited and not meant for customer data | Commercial: paid NVIDIA NIM, Groq Dev, or a self-hosted model; review the DPA |
| **G4. PII and secret scrubbing in prompts** (6.2) | Customers paste tokens, emails and hostnames; today these reach the LLM provider | Small to medium: reuse the log redaction patterns on prompt text and add email/IP masking, with tests |
| **G5. Backups and CI** (4.6, 8.2) | One disk failure loses the knowledge base and evaluation history; nothing stops a regression from merging | Small: nightly `pg_dump` with a restore test; a CI workflow running the existing lint, test and audit targets |

### P1: needed to make the tool genuinely useful day to day

| Gap | Why it matters | Effort |
|---|---|---|
| **G6. Asynchronous analysis** (1.11, 1.12) | A 1–3 minute blocking request is poor UX and fragile | Medium: job table plus polling (the ingestion and evaluation pattern already exists) |
| **G7. Draft coverage** (1.5) | 40% of answerable tickets get no draft | Medium: allow partial answers with stated gaps; store flagged identifiers to measure false positives; try `LLM_ENABLE_THINKING` and prompt variants; test the lower lexical weight |
| **G8. Answer quality measurement** (1.6, 1.7) | Safety is proven; helpfulness is not | Medium: human grading of about 35 existing drafts, then an LLM-judge metric in the evaluation runner; evaluate on real (anonymised) historical tickets |
| **G9. Engineer feedback loop** (1.8) | Without approve, edit or reject there is no learning signal and no record of what customers received | Medium: feedback table, endpoints, UI buttons; add to evaluation data |
| **G10. Metrics, alerting, cost** (7.3–7.5) | Outages and quota exhaustion are only noticed by looking | Medium: Prometheus metrics (latency, outcomes, provider errors, tokens), alerts on `/ready` failures |
| **G11. Scheduled re-ingestion** (2.3, 2.5) | Answers drift as the docs change | Small: a scheduled job (cron container or host timer) plus a staleness indicator |

### P2: scale and polish

- **G12. Ticketing integration (1.9):** a webhook or app for Zendesk, Jira or GitLab issues that posts the draft as an internal note.
- **G13. Version-aware knowledge (1.10):** index docs per major version and filter by the ticket's version.
- **G14. Reranker (3.3):** plug in a cross-encoder and re-measure retrieval.
- **G15. High availability (4.8):**
  - more than one backend replica;
  - a shared rate-limit store such as Redis;
  - CPU and memory limits (4.7).
- **G16. Supply chain automation (5.10):** Dependabot or Renovate, and image scanning in CI.
- **G17. Browser E2E and accessibility (8.4, 8.5):** Playwright with axe checks.
- **G18. Load test (8.6):** measure at expected ticket volume.
- **G19. Runbook and user guide (4.10, 9.3):** operations procedures and a guide for support engineers.
- **G20. Secret manager and rotation (5.8).**

## Achieved against the original goal

The goal was a platform that turns a support ticket into a grounded, cited, validated
resolution draft from real documentation, with honest refusals, human review, and
measured quality. Against that goal:

- **Achieved:** the full pipeline works on real data with real providers, and the safety
  properties hold under test and in real runs:
  - grounding;
  - citations;
  - refusal;
  - injection containment;
  - no secrets in logs.

  Every phase of the plan is complete, reproducible from a clean clone, and documented
  with evidence.
- **Partly achieved:** usefulness.
  - Triage is strong.
  - Drafts, when produced, are trustworthy, but they appear for only 60% of answerable
    tickets and are slow.
  - Answer correctness itself has not been graded.
- **Not in scope of the build, but required for deployment:** the P0 items above.
