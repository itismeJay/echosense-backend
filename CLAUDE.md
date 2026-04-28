# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (auto-reload)
uvicorn app.main:app --reload

# Seed admin user (run once after DB is available)
python seed_users.py

# Production start (used by Render)
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

There is no test suite currently. No linter is configured.

## Environment

Requires a `.env` file at the project root:

```
DATABASE_URL=postgresql://user:password@host/dbname
SECRET_KEY=your-secret-key
```

## Architecture

**Entry point**: `app/main.py` — registers all routers and runs `Base.metadata.create_all` on startup, so new SQLAlchemy models are auto-migrated when the server starts (no Alembic).

**Database** (`app/database.py`): Async SQLAlchemy with asyncpg. The `DATABASE_URL` from settings is rewritten at import time to swap `postgresql://` → `postgresql+asyncpg://` and strip SSL params. Use `get_db()` as a FastAPI dependency to get an `AsyncSession`.

**Config** (`app/config.py`): pydantic-settings `Settings` class. `SECRET_KEY` defaults to `"echosense-secret-key"` if not set in env — override in production.

**Layer pattern**: models (`app/models/`) define SQLAlchemy ORM tables, schemas (`app/schemas/`) define Pydantic request/response shapes, routers (`app/routers/`) contain endpoint logic.

**Auth** (`app/routers/auth.py`): JWT via `python-jose` (HS256, 24 h expiry). `POST /auth/login` returns a `TokenResponse` with `access_token` + `user`. `GET /auth/me` reads the `Authorization: Bearer <token>` header directly (not FastAPI's `OAuth2PasswordBearer`) and returns `UserOut`. Passwords hashed with passlib bcrypt.

**Notifications** (`app/notifications/push.py`): currently a logging stub — no external push service is wired up.

**Deployment**: Render.com via `render.yaml`. Build command is `pip install -r requirements.txt`; `DATABASE_URL` and `SECRET_KEY` are injected as env vars.
