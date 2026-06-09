import threading
from collections import deque
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.models.user import User
from app.routers.auth import require_admin

router = APIRouter(prefix="/system", tags=["System Logs"])

_log_lines: deque = deque(maxlen=500)
_log_lock = threading.Lock()
_log_counter = 0


def classify_line(message: str) -> str:
    if "[ALERT]" in message:
        return "alert"
    elif "[STT] HIT" in message:
        return "hit"
    elif "[STT]" in message:
        return "stt"
    elif "[TRACK" in message:
        return "track"
    elif "[AUDIO]" in message:
        return "audio"
    elif "HEARTBEAT" in message:
        return "heartbeat"
    elif "[NETWORK]" in message or "[NET-" in message:
        return "network"
    elif "ERROR" in message.upper():
        return "error"
    elif "[SENDER]" in message:
        return "sender"
    else:
        return "info"


class LogIngest(BaseModel):
    lines: List[str]


class LogLine(BaseModel):
    id: int
    timestamp: datetime
    message: str
    type: str


class LogIngestResponse(BaseModel):
    status: str
    received: int


class LogQueryResponse(BaseModel):
    lines: List[LogLine]
    total: int


@router.post("/logs", response_model=LogIngestResponse)
def ingest_logs(body: LogIngest):
    global _log_counter
    now = datetime.utcnow()
    with _log_lock:
        for message in body.lines:
            _log_counter += 1
            _log_lines.append(
                LogLine(
                    id=_log_counter,
                    timestamp=now,
                    message=message,
                    type=classify_line(message),
                )
            )
    return LogIngestResponse(status="ok", received=len(body.lines))


@router.get("/logs", response_model=LogQueryResponse)
def get_logs(
    type: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    _: User = Depends(require_admin),
):
    with _log_lock:
        lines = list(_log_lines)

    if type:
        lines = [l for l in lines if l.type == type]
    if search:
        lines = [l for l in lines if search.lower() in l.message.lower()]

    lines = lines[-limit:]
    return LogQueryResponse(lines=lines, total=len(lines))
