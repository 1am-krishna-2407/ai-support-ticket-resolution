# Changelog

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
