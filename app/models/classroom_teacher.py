from sqlalchemy import Column, DateTime, ForeignKey, ForeignKeyConstraint, Integer, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ClassroomTeacher(Base):
    """A teacher (role staff) assigned to one classroom of their school.

    A teacher sees and is pushed only their classrooms' alerts. The composite foreign
    key keeps the row's school equal to the classroom's school; the API also requires it
    to be the teacher's school.
    """

    __tablename__ = "classroom_teachers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["classroom_id", "school_id"],
            ["classrooms.id", "classrooms.school_id"],
            name="fk_classroom_teachers_classroom_school",
            ondelete="CASCADE",
        ),
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    classroom_id = Column(UUID(as_uuid=True), primary_key=True, index=True)
    school_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
