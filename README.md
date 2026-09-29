# Support Resolution Platform

An AI-assisted technical support ticket resolution platform. It ingests public
technical documentation, retrieves relevant evidence with hybrid (vector +
lexical) search, and drafts grounded, cited resolutions that a human reviews.
Every output is an **AI-generated draft, not a human-approved answer**.

> **Independent project.** This project is not affiliated with, endorsed by, or
> sponsored by the provider of the documentation it indexes. Documentation
> content remains the property of its owners and is used under its published
> licence (see `docs/licensing.md`, written in Phase 2).

## Status

Built phase by phase (see `docs/CHANGELOG.md`). **Phase 1 (foundation) is
complete:** containers, configuration, database schema and migrations, error
contract, request IDs, structured logging, and the frontend shell. Ingestion,
retrieval, and AI resolution are not implemented yet; pages for them say so
rather than showing sample data.

## Quickstart

Prerequisites: Docker with Compose v2. For local tests and linting: Python 3.12
and Node.js 22+.

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD and ADMIN_TOKEN
docker compose up -d --build  # postgres (pgvector), backend, frontend
docker compose run --rm backend alembic upgrade head
```

- Frontend: http://localhost:8080
- API: http://localhost:8000/api/v1/health
- OpenAPI docs: http://localhost:8000/api/v1/docs

Migrations never run automatically on startup.

### Developer commands

`make <target>` on Linux/macOS, or `./tasks.ps1 <target>` on Windows:

| Target | What it does |
|---|---|
| `setup` | Create `backend/.venv`, install backend dev deps and frontend deps |
| `up` / `down` | Start / stop the Compose stack |
| `migrate` | `alembic upgrade head` inside the backend container |
| `test` | Backend (unit + integration) and frontend tests |
| `lint` | ruff, mypy (strict), tsc, eslint |
| `audit` | `pip-audit` and `npm audit` |

Integration tests run against a real Postgres + pgvector database named
`<POSTGRES_DB>_test`, created automatically the first time the Postgres volume
initialises. Set `TEST_DATABASE_URL` in `.env` (see `.env.example`); the
tests refuse any database whose name does not end in `_test`.

## Repository layout

```
backend/     FastAPI app (api, core, db, schemas, repositories, services, prompts, providers)
frontend/    React + TypeScript + Vite + Tailwind + TanStack Query + Zod
knowledge/   Source manifest (committed) and raw fetches (git-ignored)
evaluation/  Evaluation dataset and results
docs/        Architecture decisions, changelog, and (later) ingestion/retrieval/evaluation/security docs
infra/       Postgres init scripts
```

## Configuration

All settings are environment variables, validated at startup by
`backend/app/core/config.py`; invalid or missing required values stop the app
with a clear error. `.env.example` lists the common ones. Secrets live only in
`.env`, which is git-ignored.
