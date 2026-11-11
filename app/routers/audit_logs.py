import csv
import io
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.routers.auth import require_admin
from app.schemas.audit_log import AuditLogPage
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)
from app.services.school_access import is_global_admin

router = APIRouter(prefix="/audit-logs", tags=["Audit Logs"])

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_EXPORT_ROWS = 10_000


@dataclass(frozen=True)
class AuditLogFilters:
    search: Optional[str]
    actor_email: Optional[str]
    actor_role: Optional[str]
    action: Optional[str]
    resource: Optional[str]
    status: Optional[str]
    date_from: Optional[datetime]
    date_to: Optional[datetime]
    sort_order: str


def _parse_date_bound(
    value: Optional[str],
    field_name: str,
    *,
    end_of_day: bool,
) -> Optional[datetime]:
    if value is None:
        return None

    raw = value.strip()
    if not raw:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a valid ISO-8601 date or datetime",
        )

    try:
        if "T" not in raw and " " not in raw:
            parsed_date = date.fromisoformat(raw)
            return datetime.combine(
                parsed_date,
                time.max if end_of_day else time.min,
                tzinfo=timezone.utc,
            )

        parsed_datetime = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed_datetime.tzinfo is None:
            parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
        return parsed_datetime.astimezone(timezone.utc)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a valid ISO-8601 date or datetime",
        ) from exc


async def get_audit_log_filters(
    search: Annotated[Optional[str], Query(max_length=200)] = None,
    actor_email: Annotated[Optional[str], Query(max_length=320)] = None,
    actor_role: Annotated[Optional[str], Query(max_length=50)] = None,
    action: Annotated[Optional[str], Query(max_length=100)] = None,
    resource: Annotated[Optional[str], Query(max_length=100)] = None,
    status: Annotated[Optional[AuditStatus], Query()] = None,
    date_from: Annotated[Optional[str], Query()] = None,
    date_to: Annotated[Optional[str], Query()] = None,
    sort_order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> AuditLogFilters:
    parsed_from = _parse_date_bound(date_from, "date_from", end_of_day=False)
    parsed_to = _parse_date_bound(date_to, "date_to", end_of_day=True)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be after date_to",
        )

    return AuditLogFilters(
        search=search.strip() if search else None,
        actor_email=actor_email.strip() if actor_email else None,
        actor_role=actor_role.strip() if actor_role else None,
        action=action.strip() if action else None,
        resource=resource.strip() if resource else None,
        status=status.value if status else None,
        date_from=parsed_from,
        date_to=parsed_to,
        sort_order=sort_order,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(filters: AuditLogFilters, current_user: User | None = None):
    conditions = []
    if current_user is not None and not is_global_admin(current_user):
        conditions.append(
            AuditLog.school_id == current_user.school_id
            if current_user.school_id is not None
            else false()
        )
    if filters.search:
        term = f"%{_escape_like(filters.search)}%"
        conditions.append(
            or_(
                AuditLog.actor_email.ilike(term, escape="\\"),
                AuditLog.action.ilike(term, escape="\\"),
                AuditLog.resource.ilike(term, escape="\\"),
                AuditLog.target.ilike(term, escape="\\"),
                AuditLog.description.ilike(term, escape="\\"),
            )
        )
    if filters.actor_email:
        conditions.append(func.lower(AuditLog.actor_email) == filters.actor_email.lower())
    if filters.actor_role:
        conditions.append(func.lower(AuditLog.actor_role) == filters.actor_role.lower())
    if filters.action:
        conditions.append(func.upper(AuditLog.action) == filters.action.upper())
    if filters.resource:
        conditions.append(func.lower(AuditLog.resource) == filters.resource.lower())
    if filters.status:
        conditions.append(AuditLog.status == filters.status)
    if filters.date_from:
        conditions.append(AuditLog.occurred_at >= filters.date_from)
    if filters.date_to:
        conditions.append(AuditLog.occurred_at <= filters.date_to)
    return conditions


def _ordered_query(filters: AuditLogFilters, current_user: User | None = None):
    occurred_at = (
        AuditLog.occurred_at.asc().nullslast()
        if filters.sort_order == "asc"
        else AuditLog.occurred_at.desc().nullslast()
    )
    identifier = AuditLog.id.asc() if filters.sort_order == "asc" else AuditLog.id.desc()
    return (
        select(AuditLog)
        .where(*_conditions(filters, current_user))
        .order_by(occurred_at, identifier)
    )


@router.get("", response_model=AuditLogPage)
async def get_audit_logs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    filters: AuditLogFilters = Depends(get_audit_log_filters),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    conditions = _conditions(filters, current_user)
    total_result = await db.execute(select(func.count(AuditLog.id)).where(*conditions))
    total = int(total_result.scalar_one())

    result = await db.execute(
        _ordered_query(filters, current_user).offset((page - 1) * page_size).limit(page_size)
    )
    items = result.scalars().all()
    return AuditLogPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def _utc_csv_value(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _csv_safe(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


@router.get("/export")
async def export_audit_logs(
    request: Request,
    filters: AuditLogFilters = Depends(get_audit_log_filters),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    conditions = _conditions(filters, current_user)
    total_result = await db.execute(select(func.count(AuditLog.id)).where(*conditions))
    total = int(total_result.scalar_one())
    if total > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Export exceeds the maximum of {MAX_EXPORT_ROWS} records; "
                "narrow the filters and try again"
            ),
        )

    # The count is checked first for a clear error, and the query has its own
    # hard ceiling so concurrent inserts cannot bypass the export limit.
    result = await db.execute(_ordered_query(filters, current_user).limit(MAX_EXPORT_ROWS + 1))
    records = result.scalars().all()
    if len(records) > MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Export exceeds the maximum of {MAX_EXPORT_ROWS} records; "
                "narrow the filters and try again"
            ),
        )

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "occurred_at",
            "actor_email",
            "actor_role",
            "action",
            "resource",
            "resource_id",
            "target",
            "status",
            "description",
            "ip_address",
            "user_agent",
            "request_id",
        ]
    )
    for record in records:
        writer.writerow(
            [
                _utc_csv_value(record.occurred_at),
                _csv_safe(record.actor_email),
                _csv_safe(record.actor_role),
                _csv_safe(record.action),
                _csv_safe(record.resource),
                _csv_safe(record.resource_id),
                _csv_safe(record.target),
                _csv_safe(record.status),
                _csv_safe(record.description),
                _csv_safe(record.ip_address),
                _csv_safe(record.user_agent),
                _csv_safe(record.request_id),
            ]
        )

    await record_audit_event(
        db,
        request,
        AuditAction.EXPORT_AUDIT_LOGS,
        AuditResource.AUDIT_LOG,
        AuditStatus.SUCCESS,
        actor=current_user,
        description="Administrator exported audit logs.",
        metadata={"exported_rows": total},
    )
    await db.commit()

    filename = f"echosense-audit-logs-{datetime.now(timezone.utc):%Y%m%d}-UTC.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
