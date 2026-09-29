# Developer entry points. On Windows without make, use ./tasks.ps1 <target> instead.
.PHONY: help setup up down logs migrate migrate-down ingest eval test test-backend test-frontend lint audit

ifeq ($(OS),Windows_NT)
PY := backend/.venv/Scripts/python
else
PY := backend/.venv/bin/python
endif

help:
	@echo "Targets: setup up down logs migrate migrate-down ingest eval test lint audit"

setup: ## Create the backend venv and install frontend deps for local tests/linting
	python -m venv backend/.venv
	$(PY) -m pip install -e "./backend[dev]"
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

eval:
	@echo "Evaluation is implemented in Phase 7 (python -m app.scripts.evaluate)." && exit 1

test: test-backend test-frontend

test-backend: ## Unit + integration tests (integration needs TEST_DATABASE_URL and a running postgres)
	cd backend && $(abspath $(PY)) -m pytest

test-frontend:
	cd frontend && npm test

lint:
	cd backend && $(abspath $(PY)) -m ruff check . && $(abspath $(PY)) -m ruff format --check . && $(abspath $(PY)) -m mypy app
	cd frontend && npm run typecheck && npm run lint

audit: ## Dependency vulnerability audit
	cd backend && $(abspath $(PY)) -m pip_audit
	cd frontend && npm audit --omit=dev
