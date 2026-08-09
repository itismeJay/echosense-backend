from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    NotificationRecipientAudit,
    ProviderTestDryRunRequest,
    ProviderTestDryRunResponse,
    ProviderTestSendRequest,
    ProviderTestSendResponse,
    PushTokenRequest,
    RegisterRequest,
    UserOut,
)
from app.routers.auth import require_admin, get_current_user, pwd_context
from app.services.notification_recipients import resolve_notification_recipients
from app.notifications.push import (
    PROVIDER_TEST_DATA_KEYS,
    provider_test_payload_preview,
    submit_expo_provider_test,
)
from app.notifications.tokens import is_structurally_valid_push_token, normalize_push_token
from app.services.provider_test import (
    ProviderTestGateError,
    consume_provider_test_dry_run,
    create_provider_test_dry_run,
)
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)

router = APIRouter(prefix="/users", tags=["Users"])


def _mask_email(email: str | None) -> str | None:
    if not isinstance(email, str) or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    if not local or not domain:
        return None
    if len(local) == 1:
        masked_local = "*"
    elif len(local) == 2:
        masked_local = f"{local[0]}*"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def _recipient_internal_id(user_id: int | None) -> str | None:
    return f"user_id:{user_id}" if user_id is not None else None


def _notification_recipient_audit(selection) -> NotificationRecipientAudit:
    return NotificationRecipientAudit(
        controlled_test_mode=selection.controlled_test_mode,
        configured_user_reference_present=selection.configured_user_reference_present,
        configured_recipient_resolved=selection.configured_recipient_resolved,
        selected_recipient_count=selection.selected_recipient_count,
        eligible_recipient_count=selection.eligible_recipient_count,
        recipient_identifier_masked=selection.recipient_identifier_masked,
        recipient_internal_id=_recipient_internal_id(selection.recipient_user_id),
        masked_email=_mask_email(selection.recipient_email),
        role=selection.recipient_role,
        account_active_status=selection.account_active_status,
        has_push_token=selection.has_push_token,
        token_structurally_valid=selection.token_structurally_valid,
        token_provider=selection.token_provider,
        token_duplicate_count=selection.token_duplicate_count,
        token_last_updated="not_recorded",
        token_stale_status="not_recorded",
        selected_recipient_source=selection.selected_recipient_source,
        broadcast_risk=not selection.controlled_test_mode,
        failure_reason=selection.failure_reason,
    )


@router.post("/push-token")
async def save_push_token(
    body: PushTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    normalized_token = normalize_push_token(body.token)
    if normalized_token is not None and not is_structurally_valid_push_token(normalized_token):
        raise HTTPException(status_code=422, detail="Invalid Expo push token")
    current_user.push_token = normalized_token
    await db.commit()
    if normalized_token is None:
        return {"message": "Push token detached"}
    return {"message": "Push token saved"}


@router.post("/", response_model=UserOut, status_code=201)
async def create_user(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=body.email,
        hashed_password=pwd_context.hash(body.password),
        role=body.role,
    )
    db.add(user)
    await db.flush()
    await record_audit_event(
        db,
        request,
        AuditAction.CREATE_USER,
        AuditResource.USER,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=user.id,
        target=user.email,
        description="Administrator created a user account.",
        metadata={"role": user.role},
    )
    await db.commit()
    await db.refresh(user)
    return UserOut(id=str(user.id), email=user.email, role=user.role)


@router.get("/", response_model=List[UserOut])
async def get_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User))
    return [UserOut(id=str(u.id), email=u.email, role=u.role) for u in result.scalars().all()]


@router.get(
    "/notification-recipient-audit",
    response_model=NotificationRecipientAudit,
)
async def audit_notification_recipient(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    selection = await resolve_notification_recipients(db, emit_log=False)
    audit = _notification_recipient_audit(selection)
    if selection.controlled_test_mode and (
        selection.failure_reason is not None or selection.eligible_recipient_count != 1
    ):
        raise HTTPException(status_code=409, detail=audit.model_dump())
    return audit


@router.post(
    "/provider-test/dry-run",
    response_model=ProviderTestDryRunResponse,
)
async def dry_run_provider_test(
    body: ProviderTestDryRunRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    selection = await resolve_notification_recipients(db, emit_log=False)
    try:
        test_id = create_provider_test_dry_run(
            selection,
            confirmed_recipient_user_id=body.confirmed_recipient_user_id,
            physical_device_confirmed=body.physical_device_confirmed,
        )
    except ProviderTestGateError as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason}) from exc

    preview = provider_test_payload_preview(test_id)
    return ProviderTestDryRunResponse(
        test_id=test_id,
        controlled_test_mode=True,
        recipient_internal_id=_recipient_internal_id(selection.recipient_user_id),
        masked_email=_mask_email(selection.recipient_email),
        role=selection.recipient_role,
        account_active_status=selection.account_active_status,
        token_present=selection.has_push_token,
        token_structurally_valid=selection.token_structurally_valid,
        token_provider=selection.token_provider,
        token_duplicate_count=selection.token_duplicate_count,
        recipient_count=1,
        payload=preview,
        payload_data_keys=sorted(PROVIDER_TEST_DATA_KEYS),
        expected_provider_submissions=1,
        expected_recipients=1,
        expected_alert_rows=0,
        expected_event_ids=0,
        expected_classroom_analytics_writes=0,
    )


@router.post(
    "/provider-test/send",
    response_model=ProviderTestSendResponse,
)
async def send_provider_test(
    body: ProviderTestSendRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    selection = await resolve_notification_recipients(db, emit_log=False)
    try:
        token = consume_provider_test_dry_run(
            selection,
            test_id=body.test_id,
            confirmed_recipient_user_id=body.confirmed_recipient_user_id,
            physical_device_confirmed=body.physical_device_confirmed,
            approve_single_send=body.approve_single_send,
        )
    except ProviderTestGateError as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason}) from exc

    result = await submit_expo_provider_test(token, body.test_id)
    result_message = (
        "Provider accepted the notification submission."
        if result.provider_classification == "accepted"
        else "Provider did not confirm acceptance of the notification submission."
    )
    return ProviderTestSendResponse(
        submission_timestamp=result.submission_timestamp,
        test_id=result.test_id,
        masked_recipient=_mask_email(selection.recipient_email),
        selected_recipient_count=result.selected_recipient_count,
        provider_http_status=result.provider_http_status,
        provider_classification=result.provider_classification,
        provider_ticket_id_redacted=result.provider_ticket_id_redacted,
        message_count=result.message_count,
        result_message=result_message,
    )


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    target_email = user.email
    target_role = user.role
    await db.delete(user)
    await record_audit_event(
        db,
        request,
        AuditAction.DELETE_USER,
        AuditResource.USER,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=user_id,
        target=target_email,
        description="Administrator deleted a user account.",
        metadata={"role": target_role},
    )
    await db.commit()
