# Repository Guidelines

## Project Structure & Module Organization

`app/main.py` creates the FastAPI application and registers endpoints from `app/routers/`. Keep database entities in `app/models/`, Pydantic request and response types in `app/schemas/`, and reusable business logic in `app/services/`. Push-notification integrations live in `app/notifications/`. Database revisions belong in `alembic/versions/`; operational helpers belong in `scripts/`. Tests mirror API and service behavior under `tests/`, while API contracts and design notes live in `docs/`.

## Build, Test, and Development Commands

Create and activate a Python 3.11 virtual environment, then run:

```bash
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
ruff check .
ruff format --check .
ECHOSENSE_TEST_DATABASE_URL=postgresql+asyncpg://... pytest
ALEMBIC_DATABASE_URL=postgresql://... alembic upgrade head
```

The development requirements include runtime packages, pytest, pytest-asyncio, and Ruff. Tests require a dedicated local PostgreSQL database and deliberately reject remote hosts or the application database. Alembic also requires an explicit migration URL.

## Coding Style & Naming Conventions

Target Python 3.11, use four-space indentation, double quotes, and a 100-character line limit. Run `ruff format .` before submitting changes and `ruff check .` to catch configured pycodestyle and Pyflakes errors. Use `snake_case` for modules, functions, fixtures, and fields; `PascalCase` for classes and ORM/Pydantic models; and `UPPER_SNAKE_CASE` for constants. Keep route handlers thin by moving reusable policy or query logic into services.

## Testing Guidelines

Pytest discovers `tests/test_*.py`; name test functions `test_<behavior>`. Async tests run automatically through `asyncio_mode = "auto"`. Add regression coverage for authorization, audit integrity, privacy, notification targeting, and schema-contract changes. External notifications are mocked globally in `tests/conftest.py`; preserve that isolation. Run the full suite against a migrated, disposable local database.

## Commit & Pull Request Guidelines

Recent history follows short Conventional Commit-style subjects such as `feat: add controlled notification targeting` and `fix: strip tzinfo before writing`. Prefer `feat:`, `fix:`, `test:`, `docs:`, or `chore:` with an imperative, focused summary.

Pull requests should explain the behavior change, identify affected endpoints or migrations, link the issue, and list validation commands. Include example request/response payloads for API contract changes. Call out new environment variables and migration or deployment steps explicitly; screenshots are only useful for rendered documentation or client-visible output.

## Security & Configuration

Copy `.env.example` locally, but never commit credentials, tokens, production URLs, or real user data. Set a strong `SECRET_KEY`. Treat audit logs as append-only, keep controlled provider testing disabled by default, and avoid weakening role checks or privacy redaction.




