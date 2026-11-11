"""Teachers assigned to classrooms: lookups for the API and the assignment rules."""

from collections import defaultdict
from typing import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.classroom import Classroom
from app.models.classroom_teacher import ClassroomTeacher
from app.models.user import User
from app.services.school_access import ROOM_SCOPED_ROLES

ONLY_TEACHERS = "Only teachers are assigned to classrooms"
NO_SCHOOL = "This person is not assigned to a school"
UNKNOWN_CLASSROOM = "Every classroom must be an active classroom of this person's school"


async def classroom_ids_by_user(db: AsyncSession, user_ids: Iterable[int]) -> dict[int, list[UUID]]:
    ids = list(set(user_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(ClassroomTeacher.user_id, ClassroomTeacher.classroom_id)
        .join(Classroom, Classroom.id == ClassroomTeacher.classroom_id)
        .where(ClassroomTeacher.user_id.in_(ids))
        .order_by(Classroom.name)
    )
    found: dict[int, list[UUID]] = defaultdict(list)
    for user_id, classroom_id in rows.all():
        found[user_id].append(classroom_id)
    return dict(found)


async def teachers_by_classroom(
    db: AsyncSession, classroom_ids: Iterable[UUID]
) -> dict[UUID, list[dict]]:
    ids = list(set(classroom_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(ClassroomTeacher.classroom_id, User.id, User.email)
        .join(User, User.id == ClassroomTeacher.user_id)
        .where(ClassroomTeacher.classroom_id.in_(ids))
        .order_by(User.email)
    )
    found: dict[UUID, list[dict]] = defaultdict(list)
    for classroom_id, user_id, email in rows.all():
        found[classroom_id].append({"id": str(user_id), "email": email})
    return dict(found)


async def set_teacher_classrooms(
    db: AsyncSession, teacher: User, classroom_ids: Iterable[UUID]
) -> tuple[list[UUID], list[UUID]]:
    """Replace a teacher's classrooms. Returns (added, removed). Does not commit."""

    if teacher.role not in ROOM_SCOPED_ROLES or teacher.is_super_admin:
        raise HTTPException(status_code=422, detail=ONLY_TEACHERS)
    if teacher.school_id is None:
        raise HTTPException(status_code=422, detail=NO_SCHOOL)
    wanted = set(classroom_ids)
    if wanted:
        valid = set(
            (
                await db.execute(
                    select(Classroom.id).where(
                        Classroom.id.in_(wanted),
                        Classroom.school_id == teacher.school_id,
                        Classroom.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if valid != wanted:
            raise HTTPException(status_code=422, detail=UNKNOWN_CLASSROOM)

    current = set(
        (
            await db.execute(
                select(ClassroomTeacher.classroom_id).where(ClassroomTeacher.user_id == teacher.id)
            )
        )
        .scalars()
        .all()
    )
    added = sorted(wanted - current, key=str)
    removed = sorted(current - wanted, key=str)
    if removed:
        await db.execute(
            delete(ClassroomTeacher).where(
                ClassroomTeacher.user_id == teacher.id,
                ClassroomTeacher.classroom_id.in_(removed),
            )
        )
    db.add_all(
        ClassroomTeacher(user_id=teacher.id, classroom_id=item, school_id=teacher.school_id)
        for item in added
    )
    await db.flush()
    return added, removed
