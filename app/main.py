from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.routers import alerts, logs, auth, users
from app.routers import dictionary, system_settings, audit_logs, reports
from app.database import engine, Base

# Imported so Base.metadata.create_all picks them up at startup
import app.models.slur            # noqa: F401
import app.models.system_settings  # noqa: F401
import app.models.audit_log        # noqa: F401
import app.models.report           # noqa: F401

# create_all() only creates missing tables — it never adds columns to an
# existing one. Since there is no Alembic, we apply additive column changes
# idempotently here so existing deployments pick up the new alert fields.
USER_COLUMN_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS push_token TEXT",
]

ALERT_COLUMN_MIGRATIONS = [
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS transcribed_text TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS detected_words TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS yamnet_class VARCHAR",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS yamnet_score DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS emotion VARCHAR",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rms DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS energy_variance DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS zero_crossing_rate DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS peak_to_average DOUBLE PRECISION",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS waveform_snapshot TEXT",
    # v2 Pi payload fields
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS categories TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS language VARCHAR",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS hard_hits TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS soft_hits TEXT",
]

# Seed the slur dictionary with default terms (idempotent via ON CONFLICT).
SLUR_SEED = """
INSERT INTO slur_dictionary (slur_text, language, severity_weight) VALUES
  ('putangina', 'Bisaya', 0.9),
  ('gago', 'Filipino', 0.8),
  ('stupid', 'English', 0.7),
  ('bobo', 'Filipino', 0.6),
  ('tanga', 'Filipino', 0.8),
  ('yawa', 'Bisaya', 0.85),
  ('buang', 'Bisaya', 0.75),
  ('pisti', 'Bisaya', 0.7),
  ('bastos', 'Bisaya', 0.65),
  ('ulol', 'Filipino', 0.75),
  ('tarantado', 'Filipino', 0.8),
  ('idiot', 'English', 0.7),
  ('worthless', 'English', 0.6),
  ('dumb', 'English', 0.55),
  ('fool', 'English', 0.5)
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
    title="EchoSense API",
    description="Real-Time Acoustic Aggression Detection System",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(alerts.router)
app.include_router(logs.router)
app.include_router(dictionary.router)
app.include_router(system_settings.router)
app.include_router(audit_logs.router)
app.include_router(reports.router)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in USER_COLUMN_MIGRATIONS + ALERT_COLUMN_MIGRATIONS:
            await conn.execute(text(statement))
        await conn.execute(text(SLUR_SEED))
        await conn.execute(text(SYSTEM_SETTINGS_SEED))

@app.get("/")
def root():
    return {"message": "EchoSense API is running 🎙️"}

@app.get("/health")
def health():
    return {"status": "ok"}
