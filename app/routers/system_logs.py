import threading
from collections import deque
from datetime import datetime
from typing import List, NamedTuple, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.edge_device import EdgeDevice
from app.models.user import User
from app.routers.auth import require_admin
from app.services.device_auth import (
    DEVICE_ID_HEADER,
    DEVICE_KEY_HEADER,
    authenticate_edge_device,
)
from app.services.school_access import is_global_admin

router = APIRouter(prefix="/system", tags=["System Logs"])

_log_lines: deque = deque(maxlen=500)
_log_lock = threading.Lock()
_log_counter = 0
REDACTED_STT_MESSAGE = "[STT] transcript redacted; alert evidence is available on alert records"


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


def sanitize_line(message: str) -> str:
    if "[STT]" in message:
        return REDACTED_STT_MESSAGE
    return message


class LogIngest(BaseModel):
    lines: List[str]


class LogLine(BaseModel):
    id: int
    timestamp: datetime
    message: str
    type: str


class _BufferedLine(NamedTuple):
    line: "LogLine"
    # None for lines posted without device credentials; only super admins see those.
    school_id: UUID | None


class LogIngestResponse(BaseModel):
    status: str
    received: int


class LogQueryResponse(BaseModel):
    lines: List[LogLine]
    total: int


async def optional_edge_device(
    device_code: str | None = Header(default=None, alias=DEVICE_ID_HEADER),
    device_key: str | None = Header(default=None, alias=DEVICE_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
) -> EdgeDevice | None:
    """Device credentials are optional during the edge transition, but verified if sent."""

    if device_code is None and device_key is None:
        return None
    return await authenticate_edge_device(device_code, device_key, db)


@router.post("/logs", response_model=LogIngestResponse)
async def ingest_logs(
    body: LogIngest,
    edge_device: EdgeDevice | None = Depends(optional_edge_device),
):
    global _log_counter
    now = datetime.utcnow()
    school_id = edge_device.school_id if edge_device is not None else None
    with _log_lock:
        for message in body.lines:
            _log_counter += 1
            message_type = classify_line(message)
            _log_lines.append(
                _BufferedLine(
                    LogLine(
                        id=_log_counter,
                        timestamp=now,
                        message=sanitize_line(message),
                        type=message_type,
                    ),
                    school_id,
                )
            )
    return LogIngestResponse(status="ok", received=len(body.lines))


@router.get("/logs", response_model=LogQueryResponse)
def get_logs(
    type: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(require_admin),
):
    with _log_lock:
        buffered = list(_log_lines)

    if is_global_admin(current_user):
        lines = [item.line for item in buffered]
    else:
        if current_user.school_id is None:
            raise HTTPException(status_code=403, detail="Administrator is not assigned to a school")
        lines = [item.line for item in buffered if item.school_id == current_user.school_id]

    if type:
        lines = [l for l in lines if l.type == type]
    if search:
        lines = [l for l in lines if search.lower() in l.message.lower()]

    lines = lines[-limit:]
    return LogQueryResponse(lines=lines, total=len(lines))
