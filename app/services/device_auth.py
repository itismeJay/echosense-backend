from datetime import datetime, timezone
import logging

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import get_db
from app.models.edge_device import EdgeDevice
from app.routers.auth import SCHOOL_INACTIVE, pwd_context

DEVICE_ID_HEADER = "X-EchoSense-Device-Id"
DEVICE_KEY_HEADER = "X-EchoSense-Device-Key"
INVALID_DEVICE_CREDENTIALS = "Invalid device credentials"
_DUMMY_DEVICE_KEY_HASH = pwd_context.hash("dummy-device-key-for-timing-only")
logger = logging.getLogger(__name__)


def _credential_error(status_code: int = 401) -> HTTPException:
    return HTTPException(status_code=status_code, detail=INVALID_DEVICE_CREDENTIALS)


def hash_device_key(device_key: str) -> str:
    return pwd_context.hash(device_key)


def verify_device_key(device_key: str, api_key_hash: str) -> bool:
    try:
        return pwd_context.verify(device_key, api_key_hash)
    except (TypeError, ValueError):
        return False


async def authenticate_edge_device(
    device_code: str | None = Header(default=None, alias=DEVICE_ID_HEADER),
    device_key: str | None = Header(default=None, alias=DEVICE_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
) -> EdgeDevice:
    if (
        not device_code
        or not device_code.strip()
        or not device_key
        or len(device_code) > 100
        or len(device_key) > 256
    ):
        raise _credential_error()

    result = await db.execute(
        select(EdgeDevice)
        .where(EdgeDevice.device_code == device_code.strip())
        .options(joinedload(EdgeDevice.classroom), joinedload(EdgeDevice.school))
    )
    device = result.scalar_one_or_none()
    if device is None:
        verify_device_key(device_key, _DUMMY_DEVICE_KEY_HASH)
        raise _credential_error()
    if not device.is_active:
        raise _credential_error(status_code=403)
    if not verify_device_key(device_key, device.api_key_hash):
        raise _credential_error()
    if device.school is not None and not device.school.is_active:
        raise HTTPException(status_code=403, detail=SCHOOL_INACTIVE)

    device.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(device)
    return device


async def edge_device_or_transition(
    device_code: str | None = Header(default=None, alias=DEVICE_ID_HEADER),
    device_key: str | None = Header(default=None, alias=DEVICE_KEY_HEADER),
    db: AsyncSession = Depends(get_db),
) -> EdgeDevice | None:
    """Device credentials for edge-facing calls (heartbeat, log upload, dictionary sync).

    Credentials that are sent are always verified (401/403 when wrong). A call with no
    credentials at all is refused with 401, unless ECHOSENSE_ALLOW_ANONYMOUS_EDGE is on
    for the switch-over from older edges; then it is allowed, logged, and returns None.
    """

    if device_code is None and device_key is None:
        if settings.ECHOSENSE_ALLOW_ANONYMOUS_EDGE:
            logger.warning("Anonymous edge call accepted (ECHOSENSE_ALLOW_ANONYMOUS_EDGE=true)")
            return None
        raise _credential_error()
    return await authenticate_edge_device(device_code, device_key, db)
