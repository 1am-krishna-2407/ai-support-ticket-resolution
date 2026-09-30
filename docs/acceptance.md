# Acceptance checklist

The definition of done from `docs/implementation-plan.md` §4, with the evidence for each
item. Verified on 2026-09-30 at the commit tagged `phase-9-complete`.

## Clean-room run

A fresh `git clone` of the repository, with only `.env` copied in, was built with
`docker compose -p cleanroom build --no-cache` and run as a separate Compose project with
an empty database:

1. Images built from scratch: the backend installed every Python dependency with
   `pip install --require-hashes -r requirements.lock`; the frontend ran `npm ci`
   (251 packages).
2. `alembic upgrade head` applied migrations 0001–0005 to the empty database.
3. `python -m app.scripts.ingest --only runner-configuration-tls-self-signed
   runner-register user-project-integrations-webhooks` fetched the three pages from
   docs.gitlab.com (robots.txt checked, connections pinned to the validated IP), then
   chunked, embedded and published 73 chunks. `/ready` then reported 3 documents and
   73 chunks, all embedded. A second run reported 3 unchanged documents and 0 embedding calls.
4. `E2E_BASE_URL=http://127.0.0.1:8080 E2E_LLM=1 pytest tests/e2e` passed all 11 tests,
   through nginx:
   - the SPA routes;
   - health, version and readiness;
   - hybrid, vector and lexical search finding the x509 page;
   - an out-of-scope query refused;
   - document detail and chunks;
   - error contract;
   - admin 401/403;
   - job and evaluation history;
   - a real Nemotron ticket analysis.

The clean-room project and its volume were then removed.

## Definition of done

| Item | Status | Evidence |
|---|---|---|
| `docker compose up --build` works from a clean clone; migrations and ingestion documented and working | Done | Clean-room run above; README Quickstart; `docs/ingestion.md` |
| Real documentation ingested with preserved metadata, versioning, and staged publish | Done | 217 of 217 pages and 4,600 chunks, all embedded (`/ready`); re-run: 0 changed, 0 embedding calls; `docs/ingestion.md` §9; `tests/integration/test_ingestion_service.py` |
| Vector, lexical, hybrid retrieval (optional reranker) work and are inspectable | Done | `POST /search` with score breakdowns and ranks; Retrieval Explorer page; 51-case results per method in `docs/evaluation.md`. Reranker is a no-op hook (`NoopReranker`), documented as optional |
| Classification, priority (documented rubric), structured generation, citation and grounding validation, human-review logic | Done | `docs/priority-rubric.md`; generation run: category accuracy 0.93, priority within one level 0.96, schema validity 1.0; `tests/unit/test_analysis_rules.py`, `test_ticket_analysis.py` |
| Unsupported and low-evidence tickets get a safe, honest response | Done | Correct refusal rate 1.0 (including the near-miss c45); ticket t09; nothing unsupported was published in 51 + 10 real analyses |
| All API routes implemented, validated, and documented; OpenAPI accurate | Done | 16 operations; `docs/api.md` and `docs/openapi.json` generated, with a drift test (`tests/unit/test_openapi.py`); API tests per route |
| All 7 frontend pages use real APIs; all async states handled; no fake numbers | Done | 33 component tests covering loading, empty, error, degraded and rate-limited states; with the backend stopped, System Status showed "Service unavailable" (verified in the browser) and recovered after restart |
| Evaluation runner, documented metrics, stored reproducible experiments visible in the UI | Done | Runs `retrieval-{hybrid,vector,lexical}-v2` and `generation-nemotron-v1` stored with frozen config; formulas in `docs/evaluation.md`; Evaluation page with run comparison |
| Request IDs, structured logs, no secrets or LLM payloads logged | Done | `X-Request-ID` on every response (E2E asserts it); `request_logs` rows hold metadata only (verified in the live DB); `tests/security/test_log_safety.py` |
| SSRF, prompt injection, input limits, admin protection, safe errors verified by tests | Done | `backend/tests/security` (135 tests); rate limit verified through nginx (429 + `Retry-After` after 60 searches/min) |
| Unit, integration, API, retrieval, security, and E2E tests pass | Done | Backend 473 passed (unit, integration, security), E2E 11 passed against the live stack; frontend 33 passed; ruff, mypy strict, tsc, eslint clean |
| README, architecture, ingestion, retrieval, evaluation, security, licensing, and API docs complete | Done | `README.md`, `docs/*.md` (table in the README) |
| Final quality audit clean; clean-room acceptance passes | Done | Audit below; clean-room run above |
| No required feature is a mock, placeholder, or TODO | Done | Audit below |

## Final quality audit

- **Markers:** searching `backend/app` and `frontend/src` for
  `TODO|FIXME|XXX|HACK|lorem|mock|stub|not implemented|coming soon` finds only these
  (none is a gap):
  - `NotImplementedError` in the abstract hooks of the embedding-provider base class,
    which both providers override;
  - the word "placeholder" in the grounding validator, which recognises placeholders such
    as `<your_token>` in model output;
  - test helpers.
- **Dependencies:** `pip-audit` over the locked set and `npm audit` report no known
  vulnerabilities.
- **Containers:**
  - non-root users;
  - read-only root filesystems;
  - all capabilities dropped;
  - `no-new-privileges`;
  - ports bound to 127.0.0.1.

  All confirmed with `docker inspect`.
- **Secrets:** every commit was checked for the literal values of all keys in `.env`; none
  appear in the repository.

## Known limits (accepted)

- **Draft coverage:** 60% of answerable evaluation tickets receive a published draft; the
  rest go to an engineer with stated reasons (`docs/evaluation.md`).
- **Speed:** free-tier Nemotron is slow, with a median of 59 s per analysis.
- **Single instance:** rate limits and background jobs are in-process, which suits the
  single-replica design (ADR D1, D9, D12).
- **UI tests:** end-to-end tests drive the HTTP surface through nginx rather than a
  headless browser. The UI's own states are covered by component tests and a manual
  browser check.
