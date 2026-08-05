# Jobyn AI v2 — Backend

Production-ready backend for the Jobyn AI career platform: resume parsing,
candidate matching, ATS-optimized resumes, cover letters, career coaching,
interview prep, application tracking, and career-path recommendations.

Built on FastAPI, async SQLAlchemy 2.x, Pydantic v2, Celery, and Redis, with a
Clean Architecture, repository + service layers, and PostgreSQL with a SQLite
fallback. Containerized for Google Cloud Run.

## Tech Stack

| Concern             | Choice                                                       |
| ------------------- | ------------------------------------------------------------ |
| Language            | Python 3.12+                                                 |
| Web framework       | FastAPI                                                      |
| Validation          | Pydantic v2 + pydantic-settings                              |
| ORM                 | SQLAlchemy 2.x (async, typed `Mapped[...]`)                  |
| Migrations          | Alembic (async-aware `env.py`)                               |
| Database            | PostgreSQL (asyncpg); SQLite (aiosqlite) dev fallback        |
| Async jobs          | Celery + Redis (broker + result backend + cache)             |
| Authentication      | JWT (PyJWT) + bcrypt password hashing                        |
| HTTP server         | Gunicorn + Uvicorn workers                                   |
| Testing             | pytest, pytest-asyncio, httpx `TestClient`                   |
| Quality gates       | ruff, black, mypy, GitHub Actions CI                         |

## Folder Structure

```
.
├── backend/                          # Application package
│   ├── main.py                       # FastAPI factory + ASGI entrypoint
│   ├── api/                          # Presentation layer (HTTP)
│   │   ├── deps.py                   # DI wiring: get_db, auth dependencies
│   │   ├── router.py                 # Top-level router aggregation
│   │   └── v1/
│   │       ├── router.py             # v1 router aggregation
│   │       └── endpoints/
│   │           ├── auth.py           # register / login / me
│   │           └── health.py         # /health, /health/ready
│   ├── core/                         # Cross-cutting, zero app-level deps
│   │   ├── config.py                 # pydantic-settings (single source of truth)
│   │   ├── errors.py                 # Domain exceptions + HTTP mapping
│   │   └── security.py               # JWT create/decode, password hashing
│   ├── database/
│   │   ├── base_class.py             # THE single DeclarativeBase + type map
│   │   ├── base.py                   # metadata aggregation point
│   │   ├── redis.py                  # lazy async Redis client
│   │   └── session.py                # async engine + session factory
│   ├── models/                       # ORM models (feature models land here)
│   │   ├── __init__.py               # REGISTER every model here
│   │   ├── mixins.py                 # UUID pk, timestamps, soft delete
│   │   └── user.py                   # User account model
│   ├── repositories/                 # Data access layer
│   │   ├── base.py                   # Generic async CRUD + soft-delete support
│   │   └── user.py                   # user-specific queries
│   ├── schemas/                      # Pydantic contracts
│   │   ├── auth.py                   # UserLogin, TokenResponse
│   │   ├── common.py                 # ORMModel base, pagination envelope
│   │   └── user.py                   # UserRead, UserCreate
│   ├── services/                     # Business logic layer
│   │   ├── base.py                   # Generic service base
│   │   └── user.py                   # registration + authentication
│   ├── utils/
│   │   └── logging.py                # structured logging setup
│   ├── workers/                      # Celery async workers
│   │   ├── celery_app.py             # Celery app factory
│   │   └── tasks.py                  # task registry (ping probe)
│   └── py.typed
├── alembic/
│   ├── env.py                        # async Alembic environment
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py           # empty baseline revision
├── alembic.ini
├── tests/
│   ├── conftest.py                   # in-memory SQLite + TestClient
│   ├── test_health.py
│   ├── test_repository.py            # CRUD + soft-delete on a throwaway model
│   └── test_security.py
├── .github/workflows/ci.yml          # lint, type, tests (SQLite + PostgreSQL), image build
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile                        # shared API + worker image
├── Makefile
├── docker-compose.yml                # PostgreSQL + Redis + API + worker
├── pyproject.toml                    # metadata, lint, format, typing, test config
└── requirements.txt                  # pinned runtime dependencies
```

## Architecture

Layers are strictly one-directional. Every arrow is an import allowed by the
next layer down; nothing in a lower layer ever imports a higher layer.

```
┌──────────────────────────────────────────────────────────────┐
│  api/      (HTTP endpoints, DI, request/response mapping)    │
├──────────────────────────────────────────────────────────────┤
│  services/ (business logic, orchestrates repositories)       │
├──────────────────────────────────────────────────────────────┤
│  repositories/ (data access, single model per repo)          │
├──────────────────────────────────────────────────────────────┤
│  database/ + models/ (engine, session, ORM mapping)          │
├──────────────────────────────────────────────────────────────┤
│  core/     (config, security, errors - zero dependencies)    │
└──────────────────────────────────────────────────────────────┘
```

**Flow of a request.** FastAPI injects an `AsyncSession` (`get_db`) → the
endpoint gets a service via a `Depends` factory → the service calls repository
methods → the repository returns ORM instances → the endpoint serializes them
with a Pydantic schema (`from_attributes=True`) and commits the unit of work.

**Async jobs.** Long-running AI work (resume parsing, matching, generation) is
delegated to Celery tasks in `backend/workers/`. Tasks run outside the request
lifecycle and never import FastAPI.

**Dependency injection.** The composition root is `backend/api/deps.py`.
Sessions, repositories, and services are wired with `Depends`, so nothing is
instantiated inside an endpoint and every component can be replaced with a fake
in tests.

## SQLAlchemy Conventions

- Every model inherits from the single `Base` in
  `backend/database/base_class.py` and is composed from the mixins in
  `backend/models/mixins.py` (`UUIDPrimaryKeyMixin`, `TimestampMixin`,
  `SoftDeleteMixin`).
- `type_annotation_map` maps `uuid.UUID -> Uuid` and
  `datetime -> DateTime(timezone=True)`, so models use plain type hints and
  never hardcode dialect types.
- Relationships are declared with string targets, matched `back_populates`,
  explicit `foreign_keys`, and are never duplicated.
- Every model module is registered in `backend/models/__init__.py` so
  `Base.metadata` is complete before Alembic or the application introspects it.

## How This Prevents the Four Failure Modes

### 1. SQLAlchemy mapper errors

One `DeclarativeBase` registry, portable type mapping via `type_annotation_map`,
lazy string relationship targets, explicit `back_populates` — see the
conventions above. No model can ever be configured twice or against a second
registry.

### 2. Circular imports

The import graph is a DAG rooted at `core/`, which imports nothing from the
application. New models import `Base` from `backend/database/base_class.py` (a
leaf), never the other way around. `backend/database/base.py` imports
`backend.models` (aggregation), never the reverse.

### 3. Relationship conflicts

Single registration point (`backend/models/__init__.py`) plus a single
metadata source (`Base.metadata` read by `alembic/env.py`). No partial or
duplicate relationship declarations.

### 4. Duplicated business logic

- `BaseRepository` owns generic CRUD and soft-delete filtering; feature
  repositories only add domain queries.
- `BaseService` provides shared transaction helpers; feature services contain
  only business rules.
- `core/errors.py` is the only place domain errors become HTTP responses.
- `core/security.py` is the only place tokens are signed/verified and passwords
  are hashed.

## Quick Start (SQLite fallback, no external services)

```bash
cp .env.example .env
make install
```

Point the app at SQLite by setting the URL in `.env`:

```env
DATABASE_URL=sqlite+aiosqlite:///./jobyn.db
```

Apply migrations and start the API:

```bash
make migrate
make dev
```

Open `http://localhost:8000/docs`. Health probes:
`GET /api/v1/health` and `GET /api/v1/health/ready`.

## Running Everything with docker-compose

```bash
cp .env.example .env
docker compose up --build
```

Compose starts PostgreSQL, Redis, the API (migrations run on boot), and a
Celery worker. API is served on `http://localhost:8000`.

## Celery Workers

Start a worker against a running Redis broker:

```bash
make worker
```

Verify wiring end-to-end:

```bash
celery -A backend.workers.celery_app:celery_app call workers.ping
```

The API starts and serves normally even when Redis/Celery are not running; only
the tasks that dispatch work require the broker.

## Alembic Workflow

```bash
# After adding/editing a model, generate a migration:
make revision msg="add users table"

# Review the generated file, then apply it:
make migrate
```

Migrating to SQLite and PostgreSQL interchangeably is supported:
`render_as_batch` and `compare_type` are configured automatically in
`alembic/env.py`.

## Authentication Contract

Implemented end-to-end through the architecture (model → repository → service
→ schemas → DI → endpoints):

- `POST /api/v1/auth/register` — create an account (201, normalized unique
  email, bcrypt-hashed password).
- `POST /api/v1/auth/login` — exchange credentials for an access token.
- `GET /api/v1/auth/me` — return the authenticated account (requires a token).

- `backend/core/security.py` exposes `create_access_token(subject, ...)` and
  `decode_token(token)`; `backend/services/user.py` owns password hashing,
  email normalization, uniqueness checks, and credential verification.
- The JWT carries `sub` (the user UUID), `iat`, `exp`, and `type`.
- Protected endpoints use the `get_current_user` dependency from
  `backend/api/deps.py`, which validates the `Authorization: Bearer <jwt>`
  header and resolves the subject against the `User` model.

## Quality Gates

```bash
make check        # ruff + black --check + mypy + pytest
make lint         # ruff check .
make format       # black .
make typecheck    # mypy backend
make test         # pytest
```

GitHub Actions runs all gates on every push/PR, runs the suite against both
SQLite and PostgreSQL, and builds the Docker image.

## Deploying to Google Cloud Run

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/jobyn-ai-v2
gcloud run deploy jobyn-ai-v2 \
  --image gcr.io/$PROJECT_ID/jobyn-ai-v2 \
  --region $REGION \
  --set-env-vars "ENVIRONMENT=production,LOG_LEVEL=INFO" \
  --set-secrets "SECRET_KEY=jobyn-secret-key:latest" \
  --set-secrets "DATABASE_URL=jobyn-db-url:latest" \
  --set-secrets "REDIS_URL=jobyn-redis-url:latest" \
  --port 8080 \
  --allow-unauthenticated
```

Notes:

- The container listens on `$PORT` (defaults to `8080`), runs
  `alembic upgrade head` before boot, and serves via Gunicorn + Uvicorn workers.
- For larger deployments, run migrations as a separate Cloud Run job with the
  same image and command `alembic upgrade head`, and drop the auto-migrate from
  the serving container.
- Run Celery workers on Cloud Run jobs or a dedicated worker service using
  `celery -A backend.workers.celery_app:celery_app worker`.
- Keep `SECRET_KEY`, `DATABASE_URL`, and `REDIS_URL` in Secret Manager, never
  in the image.
- Scale from zero is safe: the engine uses `pool_pre_ping` and connection pools
  are sized for a single request burst.

## Where Feature Code Goes Next

The authentication module demonstrates the full pattern: model
(`backend/models/user.py`) → repository (`backend/repositories/user.py`) →
service (`backend/services/user.py`) → schemas (`backend/schemas/`) → DI
factories (`backend/api/deps.py`) → endpoints (`backend/api/v1/endpoints/`) →
migration (`alembic/versions/`). Follow the same steps for each new feature:

1. Define an ORM model in `backend/models/<feature>.py` using the mixins.
2. Register it in `backend/models/__init__.py` (mandatory for migrations).
3. Add a `BaseRepository` subclass in `backend/repositories/`.
4. Add a `BaseService` subclass in `backend/services/`.
5. Define request/response schemas in `backend/schemas/<feature>.py`.
6. Add factory dependencies in `backend/api/deps.py`.
7. Create endpoints in `backend/api/v1/endpoints/` and include the router in
   `backend/api/v1/router.py`.
8. Move heavy AI work into a task in `backend/workers/`.
9. Generate and review an Alembic migration.
