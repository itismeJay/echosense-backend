# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (auto-reload)
uvicorn app.main:app --reload

# Production start (used by Render)
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Do not run `seed_users.py` outside a disposable local environment. It requires explicit
operator-provided credentials and makes super-admin access opt-in. Tests use pytest and Ruff is
configured in `pyproject.toml`.

## Environment

Requires a `.env` file at the project root:

```
DATABASE_URL=postgresql+asyncpg://user:password@host/dbname
SECRET_KEY=your-secret-key
RUN_LEGACY_STARTUP_MAINTENANCE=false
```

## Architecture

**Entry point**: `app/main.py` — registers all routers. Alembic is the required
schema-management path; legacy startup maintenance is disabled by default.

**Database** (`app/database.py`): Async SQLAlchemy with asyncpg. The `DATABASE_URL` from settings is rewritten at import time to swap `postgresql://` → `postgresql+asyncpg://` and strip SSL params. Use `get_db()` as a FastAPI dependency to get an `AsyncSession`.

**Config** (`app/config.py`): pydantic-settings `Settings` class. `DATABASE_URL`
and `SECRET_KEY` are required and have no production fallback.

**Layer pattern**: models (`app/models/`) define SQLAlchemy ORM tables, schemas (`app/schemas/`) define Pydantic request/response shapes, routers (`app/routers/`) contain endpoint logic.

**Auth** (`app/routers/auth.py`): JWT via `python-jose` (HS256, 24 h expiry). `POST /auth/login` returns a `TokenResponse` with `access_token` + `user`. `GET /auth/me` reads the `Authorization: Bearer <token>` header directly (not FastAPI's `OAuth2PasswordBearer`) and returns `UserOut`. Passwords hashed with passlib bcrypt.

**Notifications** (`app/notifications/push.py`): submits privacy-minimized messages
to Expo, parses tickets, and records minimum attempt status. It does not poll Expo
receipts or prove phone delivery.

**Deployment**: Render.com via `render.yaml`. Build command is `pip install -r requirements.txt`; `DATABASE_URL` and `SECRET_KEY` are injected as env vars.

**Per-room alerts** (`classroom_teachers`, migration 0017): a teacher (`role=staff`) sees only alerts
of their assigned classrooms (`scope_alert_query`) and is pushed only those
(`resolve_notification_recipients(for_alert=True, classroom_id=...)`); admins and counselors keep the
whole school. `PUT /users/{id}/classrooms` replaces a teacher's rooms (audited
`ASSIGN_TEACHER_CLASSROOMS`); classroom responses list `teachers`.

**Password reset**: `POST /users/{id}/reset-password` (admins; super admins inside a school) sets a
generated temporary password, returned once, with `must_change_password=true` (audited
`RESET_PASSWORD`, never the password). Nobody resets a super admin or themselves here.

**Device config**: `GET /devices/me/config` also lists `disabled_terms` (the school's turned-off
global words), so the edge can drop them from its built-in lists too.
