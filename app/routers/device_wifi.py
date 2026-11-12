"""Dashboard-managed Wi-Fi networks for edge devices, delivered as a pull command queue.

Admins (super admins: any school; school admins: their own) manage networks; every write
queues a command the device pulls with its key. Counselors and teachers get read-only
connection status. Passwords are Fernet-encrypted at rest and leave the server only in
the device's own command pull; they are never returned to users, logged or audited.
"""

from datetime import datetime, timezone
from ipaddress import ip_address
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device_wifi import DeviceWifiCommand, DeviceWifiNetwork
from app.models.edge_device import EdgeDevice
from app.models.user import User
from app.routers.auth import require_admin, require_alert_reviewer
from app.routers.devices import _get_device
from app.schemas.device_wifi import (
    DeviceWifiOut,
    EdgeWifiCommand,
    EdgeWifiCommandResult,
    EdgeWifiCommandResultOut,
    EdgeWifiCommandsOut,
    WifiCommandOut,
    WifiConnectRequest,
    WifiCurrentConnection,
    WifiNetworkCreate,
    WifiNetworkOut,
    WifiNetworkUpdate,
)
from app.services.audit import AuditAction, AuditResource, AuditStatus, record_audit_event
from app.services.device_auth import authenticate_edge_device
from app.services.device_status import is_device_online
from app.services.wifi_secrets import decrypt_wifi_password, encrypt_wifi_password

router = APIRouter(prefix="/devices", tags=["Device Wi-Fi"])
edge_router = APIRouter(prefix="/device/wifi", tags=["Device Wi-Fi (edge)"])

MAX_NETWORKS_PER_DEVICE = 20
MAX_COMMANDS_PER_PULL = 50
NETWORK_NOT_FOUND = "Wi-Fi network not found"

ModelT = TypeVar("ModelT", bound=BaseModel)


def _validated(model: type[ModelT], payload) -> ModelT:
    """Validate without echoing input back: FastAPI's default 422 would include the password."""

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        detail = [
            {"loc": list(error["loc"]), "msg": error["msg"]}
            for error in exc.errors(include_input=False, include_url=False, include_context=False)
        ]
        raise HTTPException(status_code=422, detail=detail) from None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _can_manage(user: User) -> bool:
    return user.role == "admin"


async def _get_network(db: AsyncSession, device_id: UUID, ssid: str) -> DeviceWifiNetwork | None:
    return await db.scalar(
        select(DeviceWifiNetwork).where(
            DeviceWifiNetwork.device_id == device_id, DeviceWifiNetwork.ssid == ssid
        )
    )


def _queue(
    db: AsyncSession,
    device: EdgeDevice,
    user: User,
    action: str,
    ssid: str,
    *,
    priority: int | None = None,
    password_encrypted: str | None = None,
) -> DeviceWifiCommand:
    payload = {"ssid": ssid}
    if priority is not None:
        payload["priority"] = priority
    command = DeviceWifiCommand(
        device_id=device.id,
        action=action,
        ssid=ssid,
        payload=payload,
        password_encrypted=password_encrypted,
        status="pending",
        created_by=user.id,
        created_at=_now(),
    )
    db.add(command)
    return command


async def _audit(
    db: AsyncSession,
    request: Request,
    user: User,
    device: EdgeDevice,
    action: AuditAction,
    command: str,
    ssid: str,
    description: str,
    **extra,
) -> None:
    # Never pass a password here; the audit sanitizer also drops *password* keys.
    await record_audit_event(
        db,
        request,
        action,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=user,
        resource_id=device.id,
        target=device.device_code,
        description=description,
        metadata={"ssid": ssid, "command": command, **extra},
        school_id=device.school_id,
    )


def _command_out(command: DeviceWifiCommand) -> WifiCommandOut:
    return WifiCommandOut(
        id=command.id,
        action=command.action,
        ssid=command.ssid,
        priority=(command.payload or {}).get("priority"),
        status=command.status,
        error=command.error,
        created_at=command.created_at,
        applied_at=command.applied_at,
    )


@router.get("/{device_id}/wifi", response_model=DeviceWifiOut)
async def read_device_wifi(
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_alert_reviewer),
):
    device = await _get_device(device_id, db, current_user)
    current = WifiCurrentConnection(
        ssid=device.wifi_ssid,
        wifi_status=device.wifi_status,
        signal=device.wifi_signal,
        online=device.is_active and is_device_online(device.last_seen_at),
        last_seen_at=device.last_seen_at,
    )
    can_manage = _can_manage(current_user)
    networks: list[WifiNetworkOut] = []
    pending: list[WifiCommandOut] = []
    if can_manage:
        rows = (
            await db.scalars(
                select(DeviceWifiNetwork)
                .where(DeviceWifiNetwork.device_id == device.id)
                .order_by(DeviceWifiNetwork.priority.desc(), DeviceWifiNetwork.ssid)
            )
        ).all()
        networks = [
            WifiNetworkOut(
                ssid=row.ssid,
                priority=row.priority,
                has_password=row.password_encrypted is not None,
                last_status=row.last_status,
                last_error=row.last_error,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
        commands = (
            await db.scalars(
                select(DeviceWifiCommand)
                .where(DeviceWifiCommand.device_id == device.id)
                .where(DeviceWifiCommand.status == "pending")
                .order_by(DeviceWifiCommand.created_at, DeviceWifiCommand.id)
            )
        ).all()
        pending = [_command_out(command) for command in commands]
    return DeviceWifiOut(
        device_id=device.id,
        can_manage=can_manage,
        current=current,
        networks=networks,
        pending_commands=pending,
    )


@router.post("/{device_id}/wifi/networks", response_model=WifiCommandOut, status_code=202)
async def add_wifi_network(
    request: Request,
    device_id: UUID,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    body = _validated(WifiNetworkCreate, payload)
    device = await _get_device(device_id, db, current_user, for_update=True)
    if await _get_network(db, device.id, body.ssid) is not None:
        raise HTTPException(status_code=409, detail="Wi-Fi network already exists")
    count = await db.scalar(select(func.count()).where(DeviceWifiNetwork.device_id == device.id))
    if count >= MAX_NETWORKS_PER_DEVICE:
        raise HTTPException(status_code=409, detail="Too many Wi-Fi networks for this device")
    ciphertext = encrypt_wifi_password(body.password) if body.password else None
    db.add(
        DeviceWifiNetwork(
            device_id=device.id,
            ssid=body.ssid,
            priority=body.priority,
            password_encrypted=ciphertext,
            last_status="pending",
            created_by=current_user.id,
            updated_at=_now(),
        )
    )
    command = _queue(
        db,
        device,
        current_user,
        "add",
        body.ssid,
        priority=body.priority,
        password_encrypted=ciphertext,
    )
    await _audit(
        db,
        request,
        current_user,
        device,
        AuditAction.ADD_WIFI_NETWORK,
        "add",
        body.ssid,
        "Administrator added a Wi-Fi network to an edge device.",
        priority=body.priority,
        secured=ciphertext is not None,
    )
    await db.commit()
    return _command_out(command)


@router.patch(
    "/{device_id}/wifi/networks/{ssid:path}", response_model=WifiCommandOut, status_code=202
)
async def update_wifi_network(
    request: Request,
    device_id: UUID,
    ssid: str,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    body = _validated(WifiNetworkUpdate, payload)
    device = await _get_device(device_id, db, current_user, for_update=True)
    network = await _get_network(db, device.id, ssid)
    if network is None:
        raise HTTPException(status_code=404, detail=NETWORK_NOT_FOUND)
    password_changed = "password" in body.model_fields_set
    if password_changed:
        network.password_encrypted = encrypt_wifi_password(body.password) if body.password else None
    if body.priority is not None:
        network.priority = body.priority
    network.last_status = "pending"
    network.last_error = None
    network.updated_at = _now()
    action = "update" if password_changed else "set_priority"
    command = _queue(
        db,
        device,
        current_user,
        action,
        ssid,
        priority=network.priority,
        password_encrypted=network.password_encrypted if password_changed else None,
    )
    await _audit(
        db,
        request,
        current_user,
        device,
        AuditAction.UPDATE_WIFI_NETWORK,
        action,
        ssid,
        "Administrator changed a Wi-Fi network on an edge device.",
        priority=network.priority,
        passphrase_changed=password_changed,
    )
    await db.commit()
    return _command_out(command)


@router.delete(
    "/{device_id}/wifi/networks/{ssid:path}", response_model=WifiCommandOut, status_code=202
)
async def remove_wifi_network(
    request: Request,
    device_id: UUID,
    ssid: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    device = await _get_device(device_id, db, current_user, for_update=True)
    network = await _get_network(db, device.id, ssid)
    if network is None:
        raise HTTPException(status_code=404, detail=NETWORK_NOT_FOUND)
    await db.delete(network)
    command = _queue(db, device, current_user, "remove", ssid)
    await _audit(
        db,
        request,
        current_user,
        device,
        AuditAction.REMOVE_WIFI_NETWORK,
        "remove",
        ssid,
        "Administrator removed a Wi-Fi network from an edge device.",
    )
    await db.commit()
    return _command_out(command)


@router.post("/{device_id}/wifi/connect", response_model=WifiCommandOut, status_code=202)
async def connect_wifi_network(
    request: Request,
    device_id: UUID,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    body = _validated(WifiConnectRequest, payload)
    device = await _get_device(device_id, db, current_user, for_update=True)
    network = await _get_network(db, device.id, body.ssid)
    if network is None:
        raise HTTPException(status_code=404, detail=NETWORK_NOT_FOUND)
    network.last_status = "pending"
    network.last_error = None
    command = _queue(db, device, current_user, "connect_now", body.ssid)
    await _audit(
        db,
        request,
        current_user,
        device,
        AuditAction.CONNECT_WIFI_NETWORK,
        "connect_now",
        body.ssid,
        "Administrator asked an edge device to switch Wi-Fi network.",
    )
    await db.commit()
    return _command_out(command)


def _secure_transport(request: Request) -> bool:
    """HTTPS (directly or via a proxy's X-Forwarded-Proto) or a loopback/private-LAN peer."""

    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if forwarded == "https":
        return True
    try:
        peer = ip_address(request.client.host) if request.client else None
    except ValueError:
        return False
    return peer is not None and (peer.is_loopback or peer.is_private)


@edge_router.get("/commands", response_model=EdgeWifiCommandsOut)
async def pull_wifi_commands(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    device: EdgeDevice = Depends(authenticate_edge_device),
):
    """Pending commands for the calling device only, oldest first. Apply in order."""

    response.headers["Cache-Control"] = "no-store"
    secure = _secure_transport(request)
    commands = (
        await db.scalars(
            select(DeviceWifiCommand)
            .where(DeviceWifiCommand.device_id == device.id)
            .where(DeviceWifiCommand.status == "pending")
            .order_by(DeviceWifiCommand.created_at, DeviceWifiCommand.id)
            .limit(MAX_COMMANDS_PER_PULL)
        )
    ).all()
    items = []
    for command in commands:
        password = None
        withheld = False
        if command.password_encrypted is not None:
            password = decrypt_wifi_password(command.password_encrypted) if secure else None
            withheld = password is None
        items.append(
            EdgeWifiCommand(
                id=command.id,
                action=command.action,
                ssid=command.ssid,
                priority=(command.payload or {}).get("priority"),
                password=password,
                password_withheld=withheld,
                created_at=command.created_at,
            )
        )
    return EdgeWifiCommandsOut(commands=items)


@edge_router.post("/commands/{command_id}/result", response_model=EdgeWifiCommandResultOut)
async def report_wifi_command_result(
    command_id: UUID,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    device: EdgeDevice = Depends(authenticate_edge_device),
):
    body = _validated(EdgeWifiCommandResult, payload)
    command = await db.scalar(
        select(DeviceWifiCommand)
        .where(DeviceWifiCommand.id == command_id, DeviceWifiCommand.device_id == device.id)
        .with_for_update()
    )
    if command is None:
        raise HTTPException(status_code=404, detail="Wi-Fi command not found")
    if command.status != "pending":
        raise HTTPException(status_code=409, detail="Wi-Fi command already reported")
    now = _now()
    command.status = body.status
    command.error = body.error if body.status == "failed" else None
    command.applied_at = now
    command.password_encrypted = None  # delivered; the network row keeps its own copy
    if command.action != "remove":
        network = await _get_network(db, device.id, command.ssid)
        if network is not None:
            network.last_status = body.status
            network.last_error = command.error
    if "current_ssid" in body.model_fields_set:
        device.wifi_ssid = body.current_ssid
        device.wifi_status = "connected" if body.current_ssid else "disconnected"
    await db.commit()
    return EdgeWifiCommandResultOut(id=command.id, status=command.status, applied_at=now)
