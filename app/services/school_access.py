from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, false

from app.models.alert import Alert
from app.models.user import User

SCHOOL_ACCESS_DENIED = "School access denied"


def is_global_admin(user: User) -> bool:
    return user.role == "admin" and bool(user.is_super_admin)


def require_school_access(user: User, school_id: UUID) -> None:
    if is_global_admin(user):
        return
    if user.school_id is None or user.school_id != school_id:
        raise HTTPException(status_code=403, detail=SCHOOL_ACCESS_DENIED)


def resolve_school_scope(user: User, school_id: UUID | None) -> UUID | None:
    """The school a request is limited to; None means every school (super admins only).

    Super admins may pass any school_id (or none, for all schools). Everyone else is
    always limited to their own school, and passing a different school_id is a 403 so
    a foreign id can never widen or redirect their view.
    """

    if is_global_admin(user):
        return school_id
    if school_id is not None and school_id != user.school_id:
        raise HTTPException(status_code=403, detail=SCHOOL_ACCESS_DENIED)
    return user.school_id


def scope_alert_query(query: Select, user: User, school_id: UUID | None = None) -> Select:
    if is_global_admin(user):
        return query if school_id is None else query.where(Alert.school_id == school_id)
    scoped = resolve_school_scope(user, school_id)
    if scoped is None:
        return query.where(false())
    return query.where(Alert.school_id == scoped)
