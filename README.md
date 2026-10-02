# Support Resolution Platform

An AI-assisted technical support ticket resolution platform. It ingests public
technical documentation (217 GitLab documentation pages), retrieves evidence with
hybrid vector + lexical search, and drafts grounded, cited resolutions for a support
engineer to review. Every output is an **AI-generated draft, not a human-approved
answer**, and a draft whose steps are not supported by the cited documentation is
withheld rather than shown.

> **Independent project.** This project is not affiliated with, endorsed by, or
> sponsored by GitLab Inc. Documentation content remains the property of its owners and
> is used under its published licence, CC BY-SA 4.0 (see `docs/licensing.md`).

## What happens to a ticket

1. **Triage.** The LLM classifies the ticket (11 categories) and extracts eight priority
   signals; a documented, deterministic rubric turns them into a priority
   (`docs/priority-rubric.md`).
2. **Retrieval.** Exact identifiers (error strings, flags, variables, paths) are extracted,
   then semantic, full-text and exact-phrase searches are fused with Reciprocal Rank
   Fusion. If nothing clears the relevance threshold (cosine 0.45), the ticket gets an
   honest *insufficient evidence* answer and no draft is generated.
3. **Generation.** Evidence is packed into a delimited, token-budgeted context (chunks that
   look like prompt injection are excluded) and the LLM returns a strict-JSON draft where
   every step cites a source.
4. **Validation.** Citations must refer to provided sources; commands, paths and config keys
   must appear in the cited text; confidence is calibrated to the evidence. Failing drafts
   are withheld. A human-review decision lists every reason (16 kinds).

## Status

All phases are complete (`docs/CHANGELOG.md`, tags `phase-1-complete` … `phase-9-complete`).
Measured results are in `docs/evaluation.md`: retrieval over a 51-case dataset and a
full generation evaluation, plus a real 10-ticket run.

## Quickstart

Prerequisites: Docker with Compose v2. For local tests and linting: Python 3.12, Node.js
22+, and (for `make lock`) `uv`.

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD, ADMIN_TOKEN (openssl rand -hex 32), and provider keys:
#   NEMOTRON_API_KEY  NVIDIA API catalog key (nvapi-...) for the LLM (LLM_PROVIDER=nvidia)
#   HF_TOKEN          Hugging Face token for Qwen3-Embedding-0.6B embeddings
docker compose up -d --build
docker compose run --rm backend alembic upgrade head
docker compose run --rm backend python -m app.scripts.ingest     # ~217 pages; embeds once
# Create the first admin (prompts for a password of at least 12 characters):
docker compose run --rm backend python -m app.scripts.create_user \
  --email you@example.com --name "Your Name" --role admin
```

Sign in at http://localhost:8080. People can create their own (agent) accounts with
**Create an account** on the login page; `REGISTRATION_MODE` makes that `open` (default),
`approval` (an admin approves each new account on the **Users** page) or `closed` (only
admins create accounts).
**Agents** analyse tickets and see their own **History**; **admins** also manage users,
run ingestion and evaluations, and can see every user's analyses. The sidebar shows who
is signed in with their analysis stats.

- Frontend: http://localhost:8080 (Dashboard, Ticket Analysis, History, Retrieval
  Explorer, Knowledge Base, Ingestion, Evaluation, System Status, Users, Account)
- API: http://localhost:8000/api/v1 — reference in `docs/api.md`, live docs at
  http://localhost:8000/api/v1/docs

Migrations never run automatically. Without provider keys the stack still starts:
search falls back to full-text and exact-phrase matching, and ticket analysis returns
review-only responses that say why.

### Providers

| Role | Default | Alternative |
|---|---|---|
| LLM | NVIDIA `nvidia/nemotron-3-super-120b-a12b` (`LLM_PROVIDER=nvidia`, `NEMOTRON_API_KEY`) | Groq `openai/gpt-oss-120b` (`LLM_PROVIDER=groq`, `LLM_API_KEY`; free tier 200k tokens/day) |
| Embeddings | `Qwen/Qwen3-Embedding-0.6B` at 1024 dimensions via the Hugging Face router (`HF_TOKEN`) | Gemini `gemini-embedding-001` (`EMBEDDING_PROVIDER=gemini`, `EMBEDDING_API_KEY`) |

Changing the embedding model or dimension requires migration `0005` (resize) and a full
re-ingest; the app refuses to mix dimensions.

### Developer commands

`make <target>` on Linux/macOS, or `./tasks.ps1 <target>` on Windows:

| Target | What it does |
|---|---|
| `setup` | Create `backend/.venv` from the hashed dev lock; install frontend deps |
| `up` / `down` / `logs` | Start / stop the stack; follow backend logs |
| `migrate` / `migrate-down` | Alembic upgrade / downgrade one step, inside the backend container |
| `ingest` | Run ingestion (`ARGS="--force"`, `--only <document_id> ...`, `--fail-fast`) |
| `eval` | Run an evaluation (`ARGS="--method vector"`, `--generation` for LLM drafts) |
| `test` | Backend (unit, integration, security) and frontend tests |
| `test-security` | The security suite only (`backend/tests/security`) |
| `lint` | ruff, ruff format, mypy (strict), tsc, eslint |
| `audit` | `pip-audit` over the locked dependencies and `npm audit` |
| `lock` | Re-resolve the hashed Python lockfiles |
| `openapi` | Regenerate `docs/openapi.json` and `docs/api.md` |

Integration tests run against a real Postgres + pgvector database named
`<POSTGRES_DB>_test`, created the first time the Postgres volume initialises; set
`TEST_DATABASE_URL` in `.env`. The tests refuse any database whose name does not end in
`_test`. End-to-end tests run against the live stack:
`E2E_BASE_URL=http://127.0.0.1:8080 E2E_EMAIL=... E2E_PASSWORD=... pytest tests/e2e` with an
agent account (or put those values in a git-ignored `.env.e2e`); add `E2E_LLM=1` to also
analyse a ticket with the real LLM.

## Documentation

| Document | Contents |
|---|---|
| `docs/architecture.md` | Components, data flow, decisions D1–D14 |
| `docs/ingestion.md` | Manifest, fetcher, parser, chunker, versioning, run history |
| `docs/retrieval.md` | Query transformation, search methods, fusion, threshold tuning |
| `docs/evaluation.md` | Dataset, metric formulas, reproducibility, results |
| `docs/security.md` | Threat model, defences and their tests, observability, supply chain |
| `docs/api.md` / `docs/openapi.json` | Generated API reference |
| `docs/priority-rubric.md` | Deterministic priority rules |
| `docs/licensing.md` | Source licence and attribution |
| `docs/acceptance.md` | Definition-of-done checklist with evidence |
| `docs/CHANGELOG.md` | What each phase delivered |

## Repository layout

```
backend/     FastAPI app (api, core, db, schemas, repositories, services, prompts, providers),
             Alembic migrations, tests (unit, integration, security, e2e), hashed lockfiles
frontend/    React 19 + TypeScript + Vite + Tailwind + TanStack Query + Zod, served by nginx
knowledge/   Source manifest (committed) and raw fetches (git-ignored)
evaluation/  Evaluation cases, golden queries, sample tickets
docs/        Documentation listed above
infra/       Postgres init scripts
```

## Configuration

All settings are environment variables, validated at startup by
`backend/app/core/config.py`; invalid values stop the app with a clear error.
`.env.example` documents them. Secrets live only in `.env`, which is git-ignored, and are
never logged or returned by the API.

## Limits

- Designed for one support team on an internal network: local accounts (no SSO or MFA),
  in-process rate limits, one backend replica.
- Answers are only as good as the indexed documentation (217 pages); anything outside it
  is answered with *insufficient evidence*.
- Free-tier providers are slow and rate-limited: a ticket analysis with Nemotron takes
  roughly 30 seconds to 3 minutes.
