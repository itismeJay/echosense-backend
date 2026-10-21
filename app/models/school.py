from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class School(Base):
    __tablename__ = "schools"
    __table_args__ = (
        Index(
            "uq_schools_normalized_name",
            text("lower(btrim(name))"),
            unique=True,
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(200), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    classrooms = relationship("Classroom", back_populates="school", lazy="selectin")
    users = relationship("User", back_populates="school")
