from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Classroom(Base):
    __tablename__ = "classrooms"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_classrooms_id_school_id"),
        Index(
            "uq_classrooms_school_normalized_name",
            "school_id",
            text("lower(btrim(name))"),
            unique=True,
        ),
        Index("ix_classrooms_school_id", "school_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    school_id = Column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    school = relationship("School", back_populates="classrooms", lazy="joined")
    devices = relationship("EdgeDevice", back_populates="classroom", lazy="selectin")
