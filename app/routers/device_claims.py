"""QR "plug in, scan, done" device setup.

provision (super admin) -> claim into a classroom (school admin, scanning the sticker)
-> activate (the box itself, fetching its device key once). Plus move, remove and reset.
No person ever sees a device key on this path.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database import get_db
from app.models.edge_device import EdgeDevice
from app.models.user import User
from app.routers.auth import SCHOOL_INACTIVE, require_admin, require_super_admin
from app.routers.devices import (
    _assign_device,
    _device_response,
    _generate_device_key,
    _get_assignable_classroom,
    _get_device,
)
from app.schemas.device_claims import (
    ActivateRequest,
    ActivateResponse,
    ClaimRequest,
    MoveDeviceRequest,
    ProvisionedBox,
    ProvisionRequest,
    ProvisionResponse,
    RemovedDeviceResponse,
)
from app.schemas.edge_device import EdgeDeviceResponse
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    extract_ip_address,
    record_audit_event,
)
from app.services.device_auth import hash_device_key
from app.services.device_claims import (
    activate_limits,
    claim_limits,
    claim_rate_limiter,
    generate_box_secret,
    generate_claim_code,
    generate_device_code,
    hash_box_secret,
    hash_claim_code,
    normalize_claim_code,
    qr_payload,
    verify_box_secret,
    verify_claim_code,
)

router = APIRouter(tags=["Device setup"])

CLAIM_NOT_ACCEPTED = (
    "That device code and setup code don't match a box that's ready to add. "
    "Check the sticker and try again."
)
ACTIVATION_NOT_ACCEPTED = "Device setup code not accepted"
TOO_MANY_ATTEMPTS = "Too many wrong setup codes. Try again later."
_MAX_CODE_ATTEMPTS = 25


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429, detail=TOO_MANY_ATTEMPTS, headers={"Retry-After": str(retry_after)}
    )


def _issue_setup_codes(device: EdgeDevice) -> tuple[str, str]:
    claim_code = generate_claim_code()
    box_secret = generate_box_secret()
    device.claim_code_hash = hash_claim_code(normalize_claim_code(claim_code))
    device.box_secret_hash = hash_box_secret(box_secret)
    return claim_code, box_secret


def _detach_and_revoke(device: EdgeDevice, now: datetime) -> None:
    """Back to unclaimed: no school, no room, no key, no claim record."""

    device.api_key_hash = None
    device.claim_state = "unclaimed"
    device.claim_code_hash = None
    device.box_secret_hash = None
    device.school_id = None
    device.classroom_id = None
    device.legacy_school_name = None
    device.legacy_classroom_name = None
    device.assigned_at = None
    device.claimed_at = None
    device.claimed_by = None
    device.activated_at = None
    device.key_rotated_at = now
    device.updated_at = now


async def _load_device(db: AsyncSession, device_id: UUID) -> EdgeDevice:
    device = await db.get(
        EdgeDevice,
        device_id,
        options=[joinedload(EdgeDevice.classroom), joinedload(EdgeDevice.school)],
        populate_existing=True,
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


async def _device_by_code_for_update(db: AsyncSession, device_code: str) -> EdgeDevice | None:
    return (
        await db.execute(
            select(EdgeDevice)
            .where(EdgeDevice.device_code == device_code)
            .options(joinedload(EdgeDevice.classroom), joinedload(EdgeDevice.school))
            .with_for_update(of=EdgeDevice)
        )
    ).scalar_one_or_none()


@router.post("/devices/provision", response_model=ProvisionResponse, status_code=201)
async def provision_devices(
    request: Request,
    body: ProvisionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Create unclaimed boxes. Setup codes and box secrets are returned only here."""

    created: list[tuple[EdgeDevice, str, str]] = []
    for _ in range(body.count):
        for _attempt in range(_MAX_CODE_ATTEMPTS):
            device_code = generate_device_code()
            taken = await db.scalar(
                select(EdgeDevice.id).where(EdgeDevice.device_code == device_code)
            )
            if taken is None and all(device_code != item.device_code for item, _, _ in created):
                break
        else:  # pragma: no cover - astronomically unlikely with a sparse code space
            raise HTTPException(status_code=503, detail="Could not allocate a device code")
        device = EdgeDevice(
            device_code=device_code,
            display_name=body.display_name or f"EchoSense box {device_code}",
            claim_state="unclaimed",
            api_key_hash=None,
        )
        claim_code, box_secret = _issue_setup_codes(device)
        db.add(device)
        created.append((device, claim_code, box_secret))
    try:
        await db.flush()
        for device, _, _ in created:
            await record_audit_event(
                db,
                request,
                AuditAction.PROVISION_EDGE_DEVICE,
                AuditResource.EDGE_DEVICE,
                AuditStatus.SUCCESS,
                actor=current_user,
                resource_id=device.id,
                target=device.device_code,
                description="Super admin provisioned an unclaimed device box.",
                school_id=None,
            )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Device code collision; retry") from exc

    boxes = []
    for device, claim_code, box_secret in created:
        device = await _load_device(db, device.id)
        boxes.append(
            ProvisionedBox(
                device=_device_response(device),
                device_code=device.device_code,
                claim_code=claim_code,
                box_secret=box_secret,
                qr_payload=qr_payload(device.device_code, claim_code),
            )
        )
    return ProvisionResponse(boxes=boxes)


@router.post("/devices/{device_id}/reset", response_model=ProvisionedBox)
async def reset_device(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """Revoke the key, detach from school and room, and issue new setup codes."""

    device = await _get_device(device_id, db, current_user, for_update=True)
    previous = {
        "previous_school_id": device.school_id,
        "previous_classroom_id": device.classroom_id,
        "previous_status": device.status,
    }
    _detach_and_revoke(device, _now())
    claim_code, box_secret = _issue_setup_codes(device)
    await record_audit_event(
        db,
        request,
        AuditAction.RESET_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Super admin reset a device box to unclaimed with new setup codes.",
        metadata=previous,
        school_id=previous["previous_school_id"],
    )
    await db.commit()
    device = await _load_device(db, device.id)
    return ProvisionedBox(
        device=_device_response(device),
        device_code=device.device_code,
        claim_code=claim_code,
        box_secret=box_secret,
        qr_payload=qr_payload(device.device_code, claim_code),
    )


@router.post(
    "/classrooms/{classroom_id}/devices/claim",
    response_model=EdgeDeviceResponse,
    status_code=201,
)
async def claim_device(
    request: Request,
    classroom_id: UUID,
    body: ClaimRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Add a scanned box to this classroom. One-time; the box then activates itself."""

    classroom = await _get_assignable_classroom(classroom_id, db, current_user)
    ip_address = extract_ip_address(request)
    limits = claim_limits(body.device_code, current_user.id, ip_address)
    retry_after = claim_rate_limiter.retry_after(limits)
    if retry_after is not None:
        raise _too_many(retry_after)

    device = await _device_by_code_for_update(db, body.device_code)
    code_ok = verify_claim_code(body.claim_code, device.claim_code_hash if device else None)

    async def _refuse(status_code: int, detail: str, reason: str) -> HTTPException:
        await record_audit_event(
            db,
            request,
            AuditAction.CLAIM_EDGE_DEVICE,
            AuditResource.EDGE_DEVICE,
            AuditStatus.FAILURE,
            actor=current_user,
            resource_id=device.id if code_ok and device else None,
            target=body.device_code[:100],
            description="Adding a device box to a classroom was refused.",
            metadata={"reason": reason, "classroom_id": classroom.id},
            school_id=classroom.school_id,
        )
        await db.commit()
        return HTTPException(status_code=status_code, detail=detail)

    if device is None or not code_ok:
        claim_rate_limiter.record_failure(limits)
        raise await _refuse(400, CLAIM_NOT_ACCEPTED, "code_not_accepted")
    if device.claim_state != "unclaimed" or device.school_id is not None:
        same_school = device.school_id == classroom.school_id
        detail = (
            f"This box is already added to {device.classroom_name or 'a classroom'}. "
            "Use Move to change its room."
            if same_school
            else "This box is already set up in another school."
        )
        raise await _refuse(409, detail, "already_claimed")
    if not device.is_active:
        raise await _refuse(409, "This box is switched off. Ask EchoSense support.", "disabled")

    now = _now()
    _assign_device(device, classroom)
    device.claim_state = "claimed"
    device.claimed_at = now
    device.claimed_by = current_user.id
    if body.display_name:
        device.display_name = body.display_name
    await record_audit_event(
        db,
        request,
        AuditAction.CLAIM_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Administrator added a device box to a classroom.",
        metadata={"classroom_id": classroom.id, "school_id": classroom.school_id},
        school_id=classroom.school_id,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Device assignment conflict") from exc
    return _device_response(await _load_device(db, device.id))


@router.post(
    "/devices/activate",
    response_model=ActivateResponse,
    responses={202: {"model": ActivateResponse, "description": "Not added to a room yet"}},
)
async def activate_device(
    request: Request,
    response: Response,
    body: ActivateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Called by the box (no key yet). Returns its device key once, after it is claimed."""

    ip_address = extract_ip_address(request)
    limits = activate_limits(body.device_code, ip_address)
    retry_after = claim_rate_limiter.retry_after(limits)
    if retry_after is not None:
        raise _too_many(retry_after)

    device = await _device_by_code_for_update(db, body.device_code)
    code_ok = verify_claim_code(body.claim_code, device.claim_code_hash if device else None)
    secret_ok = verify_box_secret(body.box_secret, device.box_secret_hash if device else None)
    if device is None or not (code_ok and secret_ok):
        claim_rate_limiter.record_failure(limits)
        if device is not None:
            await record_audit_event(
                db,
                request,
                AuditAction.ACTIVATE_EDGE_DEVICE,
                AuditResource.EDGE_DEVICE,
                AuditStatus.FAILURE,
                actor_email=f"device:{device.device_code}",
                actor_role="device",
                resource_id=device.id,
                target=device.device_code,
                description="A device activation with wrong setup codes was refused.",
                metadata={"reason": "code_not_accepted"},
                school_id=device.school_id,
            )
            await db.commit()
        raise HTTPException(status_code=401, detail=ACTIVATION_NOT_ACCEPTED)
    if not device.is_active:
        raise HTTPException(status_code=403, detail="Device is disabled")

    now = _now()
    if device.claim_state == "unclaimed":
        # The box is on and proved its identity; staff can see it is powered up.
        device.last_seen_at = now
        await db.commit()
        response.status_code = 202
        return ActivateResponse(
            status="waiting",
            device_code=device.device_code,
            message="Waiting to be added to a classroom.",
        )
    if device.school is not None and not device.school.is_active:
        raise HTTPException(status_code=403, detail=SCHOOL_INACTIVE)

    device_key = _generate_device_key()
    device.api_key_hash = hash_device_key(device_key)
    device.claim_state = "active"
    device.claim_code_hash = None
    device.box_secret_hash = None
    device.activated_at = now
    device.key_rotated_at = now
    device.last_seen_at = now
    device.updated_at = now
    await record_audit_event(
        db,
        request,
        AuditAction.ACTIVATE_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor_email=f"device:{device.device_code}",
        actor_role="device",
        resource_id=device.id,
        target=device.device_code,
        description="A claimed device box activated and received its key.",
        metadata={"classroom_id": device.classroom_id},
        school_id=device.school_id,
    )
    await db.commit()
    return ActivateResponse(
        status="active",
        device_code=device.device_code,
        device_key=device_key,
        school_name=device.school_name,
        classroom_name=device.classroom_name,
        message="EchoSense connected.",
    )


@router.post("/devices/{device_id}/move", response_model=EdgeDeviceResponse)
async def move_device(
    request: Request,
    device_id: UUID,
    body: MoveDeviceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Move a device to another room of the same school. It keeps its key."""

    device = await _get_device(device_id, db, current_user, for_update=True)
    if device.school_id is None:
        raise HTTPException(status_code=409, detail="Add this box to a classroom first")
    classroom = await _get_assignable_classroom(body.classroom_id, db, current_user)
    if classroom.school_id != device.school_id:
        raise HTTPException(
            status_code=409, detail="A device can only move between rooms of its own school"
        )
    previous_classroom_id = device.classroom_id
    if previous_classroom_id != classroom.id:
        _assign_device(device, classroom)
        await record_audit_event(
            db,
            request,
            AuditAction.MOVE_EDGE_DEVICE,
            AuditResource.EDGE_DEVICE,
            AuditStatus.SUCCESS,
            actor=current_user,
            resource_id=device.id,
            target=device.device_code,
            description="Administrator moved a device to another classroom.",
            metadata={
                "previous_classroom_id": previous_classroom_id,
                "classroom_id": classroom.id,
            },
            school_id=device.school_id,
        )
        await db.commit()
    return _device_response(await _load_device(db, device.id))


@router.post("/devices/{device_id}/remove", response_model=RemovedDeviceResponse)
async def remove_device(
    request: Request,
    device_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Take a device out of the school: its key stops working at once.

    The box goes back to unclaimed without setup codes, so it can be added again only
    after a super admin resets it (which prints a new sticker).
    """

    device = await _get_device(device_id, db, current_user, for_update=True)
    if device.school_id is None:
        raise HTTPException(status_code=409, detail="This device is not in a school")
    previous_school_id = device.school_id
    previous_classroom_id = device.classroom_id
    _detach_and_revoke(device, _now())
    await record_audit_event(
        db,
        request,
        AuditAction.REMOVE_EDGE_DEVICE,
        AuditResource.EDGE_DEVICE,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=device.id,
        target=device.device_code,
        description="Administrator removed a device from the school; its key was revoked.",
        metadata={
            "previous_school_id": previous_school_id,
            "previous_classroom_id": previous_classroom_id,
        },
        school_id=previous_school_id,
    )
    await db.commit()
    return RemovedDeviceResponse(id=device.id, device_code=device.device_code)
