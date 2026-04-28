from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import alerts, logs, auth
from app.database import engine, Base

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
app.include_router(alerts.router)
app.include_router(logs.router)

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.get("/")
def root():
    return {"message": "EchoSense API is running 🎙️"}

@app.get("/health")
def health():
    return {"status": "ok"}