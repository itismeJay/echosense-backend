from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text
from app.routers import alerts, logs, auth, users
from app.routers import classrooms, devices
from app.routers import dictionary, system_settings, audit_logs, reports
from app.routers import system_logs
from app.config import CORS_ALLOWED_HEADERS, CORS_ALLOWED_METHODS, settings
from app.database import engine, Base

# Imported so Base.metadata.create_all picks them up at startup
import app.models.slur  # noqa: F401
import app.models.system_settings  # noqa: F401
import app.models.audit_log  # noqa: F401
import app.models.report  # noqa: F401
import app.models.edge_device  # noqa: F401
import app.models.classroom  # noqa: F401
import app.models.school  # noqa: F401

# Legacy startup maintenance is retained temporarily for the older non-Alembic
# tables. Deployments using the Alembic schema should disable it with
# RUN_LEGACY_STARTUP_MAINTENANCE=false.
USER_COLUMN_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_token TEXT",
]

ALERT_COLUMN_MIGRATIONS = [
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS event_id UUID",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS transcribed_text TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS detected_words TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS yamnet_class VARCHAR",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS yamnet_score DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS yamnet_ran BOOLEAN",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS emotion VARCHAR",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rms DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS energy_variance DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS zero_crossing_rate DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS peak_to_average DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS waveform_snapshot TEXT",
    # v2 Pi payload fields
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS categories TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS language VARCHAR",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS language_confidence DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS hard_hits TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS soft_hits TEXT",
    # v3 Pi payload fields
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS duration_gate VARCHAR(20)",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS required_duration DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS severity_evidence JSONB",
]

SYSTEM_SETTINGS_COLUMN_MIGRATIONS = [
    "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS device_status VARCHAR(20) DEFAULT 'offline'",
    "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS last_heartbeat TIMESTAMP",
    "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS vosk_version VARCHAR(50) DEFAULT 'vosk-model-small-en-us-0.15'",
    "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS yamnet_version VARCHAR(50) DEFAULT 'YAMNet TFLite v1.0'",
    "ALTER TABLE system_settings ADD COLUMN IF NOT EXISTS last_ota_update TIMESTAMP",
]

# Seed the slur dictionary with default terms (idempotent via ON CONFLICT).
SLUR_SEED = """
INSERT INTO slur_dictionary (slur_text, language, severity_weight) VALUES
  ('putangina', 'ceb', 0.9),
  ('gago', 'fil', 0.8),
  ('stupid', 'en', 0.7),
  ('bobo', 'fil', 0.6),
  ('tanga', 'fil', 0.8),
  ('yawa', 'ceb', 0.85),
  ('buang', 'ceb', 0.75),
  ('pisti', 'ceb', 0.7),
  ('bastos', 'ceb', 0.65),
  ('ulol', 'fil', 0.75),
  ('tarantado', 'fil', 0.8),
  ('idiot', 'en', 0.7),
  ('worthless', 'en', 0.6),
  ('dumb', 'en', 0.55),
  ('fool', 'en', 0.5)
ON CONFLICT (slur_text) DO NOTHING
"""

# Ensure the singleton system_settings row exists.
SYSTEM_SETTINGS_SEED = """
INSERT INTO system_settings (
  confidence_threshold, aggression_duration_threshold,
  device_status, vosk_version, yamnet_version
)
SELECT 0.55, 2.0, 'offline',
       'vosk-model-small-en-us-0.15', 'YAMNet TFLite v1.0'
WHERE NOT EXISTS (SELECT 1 FROM system_settings)
"""

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Edge-based classroom acoustic risk alerting system. "
        "Unverified possible-aggression alert. Human review required."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=list(CORS_ALLOWED_METHODS),
    allow_headers=list(CORS_ALLOWED_HEADERS),
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(alerts.router)
app.include_router(logs.router)
app.include_router(dictionary.router)
app.include_router(system_settings.router)
app.include_router(audit_logs.router)
app.include_router(reports.router)
app.include_router(system_logs.router)
app.include_router(devices.router)
app.include_router(classrooms.router)


def required_ingest_header_openapi():
    """Expose headers as required while retaining generic runtime auth errors."""

    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    required_headers = {
        "Idempotency-Key",
        "X-EchoSense-Device-Id",
        "X-EchoSense-Device-Key",
    }
    for parameter in schema["paths"]["/alerts/"]["post"].get("parameters", []):
        if parameter.get("in") == "header" and parameter.get("name") in required_headers:
            parameter["required"] = True
    app.openapi_schema = schema
    return schema


app.openapi = required_ingest_header_openapi


@app.on_event("startup")
async def startup():
    if not settings.RUN_LEGACY_STARTUP_MAINTENANCE:
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in (
            USER_COLUMN_MIGRATIONS + ALERT_COLUMN_MIGRATIONS + SYSTEM_SETTINGS_COLUMN_MIGRATIONS
        ):
            await conn.execute(text(statement))
        await conn.execute(text(SLUR_SEED))
        await conn.execute(text(SYSTEM_SETTINGS_SEED))


@app.get("/")
def root():
    return {"message": "EchoSense API is running 🎙️"}


@app.get("/health")
def health():
    return {"status": "ok"}
