PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
UVICORN ?= .venv/bin/uvicorn
CELERY ?= .venv/bin/celery
ALEMBIC ?= .venv/bin/alembic
RUFF ?= .venv/bin/ruff
BLACK ?= .venv/bin/black
MYPY ?= .venv/bin/mypy

.PHONY: help install dev worker migrate revision downgrade test lint format format-check typecheck check docker-build docker-up docker-down clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install project with dev dependencies
	uv venv --python 3.12 .venv
	uv pip install --python .venv/bin/python -e ".[dev]"

dev: ## Run the API with hot reload (SQLite by default)
	$(UVICORN) backend.main:app --reload --host 0.0.0.0 --port 8000

worker: ## Start the Celery worker
	$(CELERY) -A backend.workers.celery_app:celery_app worker --loglevel=info

migrate: ## Apply database migrations
	$(ALEMBIC) upgrade head

revision: ## Autogenerate a new migration (usage: make revision msg="add users table")
	$(ALEMBIC) revision --autogenerate -m "$(msg)"

downgrade: ## Roll back the last migration
	$(ALEMBIC) downgrade -1

test: ## Run the test suite
	$(PYTHON) -m pytest

lint: ## Lint with ruff
	$(RUFF) check .

format: ## Format code with black
	$(BLACK) .

format-check: ## Verify formatting without changing files
	$(BLACK) --check .

typecheck: ## Static type check with mypy
	$(MYPY) backend

check: lint format-check typecheck test ## Run the full quality gate

docker-build: ## Build the API image
	docker compose build

docker-up: ## Start db, redis, api and worker
	docker compose up --build

docker-down: ## Stop and remove compose services
	docker compose down

clean: ## Remove Python caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache .venv *.egg-info
