from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.routers import alerts, logs, auth, users
from app.database import engine, Base

# create_all() only creates missing tables — it never adds columns to an
# existing one. Since there is no Alembic, we apply additive column changes
# idempotently here so existing deployments pick up the new alert fields.
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
]

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

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in ALERT_COLUMN_MIGRATIONS:
            await conn.execute(text(statement))

@app.get("/")
def root():
    return {"message": "EchoSense API is running 🎙️"}

@app.get("/health")
def health():
    return {"status": "ok"}