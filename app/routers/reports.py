from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Date, cast, false, func, select
from typing import List
from app.database import get_db
from app.models.report import Report
from app.models.alert import Alert
from app.models.user import User
from app.schemas.report import ReportCreate, ReportOut
from app.routers.auth import require_admin_or_counselor
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)
from app.services.school_access import (
    is_global_admin,
    resolve_school_scope,
    scope_alert_query,
)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("", response_model=List[ReportOut], include_in_schema=False)
@router.get("/", response_model=List[ReportOut])
async def get_reports(
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_counselor),
):
    query = select(Report).order_by(Report.generated_at.desc())
    scoped = resolve_school_scope(current_user, school_id)
    if scoped is not None:
        query = query.where(Report.school_id == scoped)
    elif not is_global_admin(current_user):
        query = query.where(false())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=ReportOut, status_code=201, include_in_schema=False)
@router.post("/", response_model=ReportOut, status_code=201)
async def generate_report(
    request: Request,
    body: ReportCreate,
    school_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin_or_counselor),
):
    # A super admin inside a school reports on that school; without one, on all schools.
    report_school_id = resolve_school_scope(current_user, school_id)
    count_result = await db.execute(
        scope_alert_query(select(func.count(Alert.id)), current_user, school_id).where(
            cast(Alert.created_at, Date) >= body.date_from,
            cast(Alert.created_at, Date) <= body.date_to,
        )
    )
    total = count_result.scalar() or 0

    report = Report(
        generated_by=current_user.id,
        generated_by_email=current_user.email,
        school_id=report_school_id,
        date_from=body.date_from,
        date_to=body.date_to,
        total_incidents=total,
    )
    db.add(report)
    await db.flush()
    await record_audit_event(
        db,
        request,
        AuditAction.GENERATE_REPORT,
        AuditResource.REPORT,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=report.report_id,
        target=f"{body.date_from.isoformat()} to {body.date_to.isoformat()}",
        description="User generated an unverified alert summary report.",
        metadata={
            "date_from": body.date_from,
            "date_to": body.date_to,
            "total_incidents": total,
        },
        school_id=report_school_id,
    )
    await db.commit()
    await db.refresh(report)
    return report
