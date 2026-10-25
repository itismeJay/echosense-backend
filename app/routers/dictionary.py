from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from typing import List
from app.database import get_db
from app.languages import DictionaryLanguageCode
from app.models.slur import SlurEntry as SlurModel
from app.models.user import User
from app.schemas.dictionary import SlurEntry, SlurCreate
from app.schemas.alert import normalize_term
from app.models.alert import AlertMatchedTerm
from app.routers.auth import get_authenticated_user, get_current_user, require_admin, require_super_admin
from app.services.query_filters import escape_like
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)

router = APIRouter(prefix="/dictionary", tags=["Dictionary"])

TOTAL_COUNT_HEADER = "X-Total-Count"


async def require_admin_if_signed_in(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Signed-in readers must be admins; the edge's dictionary sync still sends no credentials."""
    if authorization is None:
        return None
    user = await get_current_user(await get_authenticated_user(authorization, db))
    return await require_admin(request, user, db)


@router.get("", response_model=List[SlurEntry], include_in_schema=False)
@router.get("/", response_model=List[SlurEntry])
async def get_dictionary(
    response: Response,
    search: str | None = Query(default=None, max_length=100),
    language: DictionaryLanguageCode | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_admin_if_signed_in),
):
    # Without parameters this still returns every term as a plain list, which the edge's
    # dictionary sync relies on. X-Total-Count reports the filtered total for paging.
    conditions = []
    if search and search.strip():
        pattern = f"%{escape_like(normalize_term(search))}%"
        conditions.append(SlurModel.slur_text.ilike(pattern, escape="\\"))
    if language is not None:
        conditions.append(SlurModel.language == language.value)

    total = await db.scalar(select(func.count(SlurModel.term_id)).where(*conditions))
    response.headers[TOTAL_COUNT_HEADER] = str(total)

    query = (
        select(SlurModel)
        .where(*conditions)
        .order_by(SlurModel.severity_weight.desc(), SlurModel.term_id)
        .offset(skip)
    )
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=SlurEntry, status_code=201, include_in_schema=False)
@router.post("/", response_model=SlurEntry, status_code=201)
async def add_slur(
    request: Request,
    body: SlurCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    normalized_text = normalize_term(body.slur_text)
    existing = await db.execute(select(SlurModel))
    if any(
        normalize_term(entry.slur_text) == normalized_text for entry in existing.scalars().all()
    ):
        raise HTTPException(status_code=409, detail="Slur already exists")

    entry = SlurModel(
        slur_text=body.slur_text.strip(),
        language=body.language.value,
        severity_weight=body.severity_weight,
    )
    db.add(entry)
    await db.flush()
    await record_audit_event(
        db,
        request,
        AuditAction.ADD_MONITORED_TERM,
        AuditResource.MONITORED_TERM,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=entry.term_id,
        target=entry.slur_text,
        description="Administrator added a monitored term.",
        metadata={
            "language": entry.language,
            "severity_weight": entry.severity_weight,
        },
    )
    await db.commit()
    await db.refresh(entry)
    return entry


@router.delete("/{term_id}")
async def delete_slur(
    request: Request,
    term_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    result = await db.execute(select(SlurModel).where(SlurModel.term_id == term_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Term not found")
    target = entry.slur_text
    language = entry.language
    severity_weight = entry.severity_weight
    relation = await db.execute(
        select(AlertMatchedTerm.id).where(AlertMatchedTerm.term_id == term_id).limit(1)
    )
    if relation.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Term is linked to an alert and cannot be deleted",
        )
    await db.delete(entry)
    await record_audit_event(
        db,
        request,
        AuditAction.DELETE_MONITORED_TERM,
        AuditResource.MONITORED_TERM,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=term_id,
        target=target,
        description="Administrator deleted a monitored term.",
        metadata={
            "language": language,
            "severity_weight": severity_weight,
        },
    )
    await db.commit()
    return {"message": "Keyword deleted"}
