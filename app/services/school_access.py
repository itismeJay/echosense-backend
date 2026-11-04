from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, false, select

from app.models.alert import Alert
from app.models.classroom_teacher import ClassroomTeacher
from app.models.user import User

# Teachers see and are pushed only their assigned classrooms' alerts.
ROOM_SCOPED_ROLES = frozenset({"staff"})

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


def is_room_scoped(user: User) -> bool:
    return user.role in ROOM_SCOPED_ROLES and not is_global_admin(user)


def assigned_classroom_ids(user: User) -> Select:
    """The classrooms a teacher is assigned to, in their own school, as a subquery."""

    return select(ClassroomTeacher.classroom_id).where(
        ClassroomTeacher.user_id == user.id,
        ClassroomTeacher.school_id == user.school_id,
    )


def scope_alert_query(query: Select, user: User, school_id: UUID | None = None) -> Select:
    """Limit an alert query to what ``user`` may see.

    Super admins: everything, or the school they entered. School admins and counselors:
    their whole school. Teachers: only alerts from their assigned classrooms (none
    until an administrator assigns a room; alerts with no classroom never).
    """

    if is_global_admin(user):
        return query if school_id is None else query.where(Alert.school_id == school_id)
    scoped = resolve_school_scope(user, school_id)
    if scoped is None:
        return query.where(false())
    query = query.where(Alert.school_id == scoped)
    if is_room_scoped(user):
        query = query.where(Alert.classroom_id.in_(assigned_classroom_ids(user)))
    return query
