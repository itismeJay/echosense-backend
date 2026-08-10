from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, false

from app.models.alert import Alert
from app.models.user import User


def is_global_admin(user: User) -> bool:
    return user.role == "admin" and bool(user.is_super_admin)


def require_school_access(user: User, school_id: UUID) -> None:
    if is_global_admin(user):
        return
    if user.school_id is None or user.school_id != school_id:
        raise HTTPException(status_code=403, detail="School access denied")


def scope_alert_query(query: Select, user: User) -> Select:
    if is_global_admin(user):
        return query
    if user.school_id is None:
        return query.where(false())
    return query.where(Alert.school_id == user.school_id)
