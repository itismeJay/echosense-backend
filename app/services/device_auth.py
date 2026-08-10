from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.models.edge_device import EdgeDevice
from app.routers.auth import pwd_context

DEVICE_ID_HEADER = "X-EchoSense-Device-Id"
DEVICE_KEY_HEADER = "X-EchoSense-Device-Key"
INVALID_DEVICE_CREDENTIALS = "Invalid device credentials"
_DUMMY_DEVICE_KEY_HASH = pwd_context.hash("dummy-device-key-for-timing-only")


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

    device.last_seen_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(device)
    return device
