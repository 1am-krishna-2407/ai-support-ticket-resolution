# Developer entry points. On Windows without make, use ./tasks.ps1 <target> instead.
.PHONY: help setup up down logs migrate migrate-down ingest eval test test-backend test-frontend test-security lint audit lock openapi

ifeq ($(OS),Windows_NT)
PY := backend/.venv/Scripts/python
else
PY := backend/.venv/bin/python
endif

help:
	@echo "Targets: setup up down logs migrate migrate-down ingest eval test test-security lint audit lock openapi"

setup: ## Create the backend venv and install frontend deps for local tests/linting
	python -m venv backend/.venv
	$(PY) -m pip install --require-hashes -r backend/requirements-dev.lock
	$(PY) -m pip install --no-deps -e ./backend
	cd frontend && npm ci

up: ## Build and start postgres, backend, frontend
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend

migrate: ## Apply database migrations (never run automatically)
	docker compose run --rm backend alembic upgrade head

migrate-down: ## Roll back one migration
	docker compose run --rm backend alembic downgrade -1

ingest: ## Fetch, parse, chunk, embed and publish the knowledge base (ARGS="--force" etc.)
	docker compose run --rm backend python -m app.scripts.ingest $(ARGS)

eval: ## Evaluate retrieval (ARGS="--method vector", "--generation" for LLM drafts)
	docker compose run --rm backend python -m app.scripts.evaluate $(ARGS)

test: test-backend test-frontend

test-backend: ## Unit + integration tests (integration needs TEST_DATABASE_URL and a running postgres)
	cd backend && $(abspath $(PY)) -m pytest

test-frontend:
	cd frontend && npm test

test-security: ## Admin protection, SSRF, injection, rate limits, safe errors, log safety
	cd backend && $(abspath $(PY)) -m pytest tests/security

lint:
	cd backend && $(abspath $(PY)) -m ruff check . && $(abspath $(PY)) -m ruff format --check . && $(abspath $(PY)) -m mypy app
	cd frontend && npm run typecheck && npm run lint

audit: ## Dependency vulnerability audit (locked Python deps and npm)
	cd backend && $(abspath $(PY)) -m pip_audit -r requirements-dev.lock --disable-pip --no-deps
	cd frontend && npm audit

lock: ## Re-resolve the hashed Python lockfiles after editing pyproject.toml
	cd backend && uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes -o requirements.lock
	cd backend && uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --generate-hashes -o requirements-dev.lock

openapi: ## Regenerate docs/openapi.json and docs/api.md
	cd backend && $(abspath $(PY)) -m app.scripts.export_openapi
