from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class EdgeDevice(Base):
    __tablename__ = "edge_devices"
    __table_args__ = (
        ForeignKeyConstraint(
            ["classroom_id", "school_id"],
            ["classrooms.id", "classrooms.school_id"],
            name="fk_edge_devices_classroom_school",
            ondelete="RESTRICT",
        ),
        Index("ix_edge_devices_school_id", "school_id"),
        Index("ix_edge_devices_classroom_id", "classroom_id"),
        Index("ix_edge_devices_is_active", "is_active"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_code = Column(String(100), unique=True, nullable=False)
    display_name = Column(String(200), nullable=False)
    # Retained for migration/backward database compatibility. The classroom FK is authoritative.
    legacy_classroom_name = Column("classroom_name", String(200), nullable=True)
    legacy_school_name = Column("school_name", String(200), nullable=True)
    school_id = Column(
        UUID(as_uuid=True),
        ForeignKey("schools.id", ondelete="RESTRICT"),
        nullable=True,
    )
    classroom_id = Column(UUID(as_uuid=True), nullable=True)
    api_key_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    key_rotated_at = Column(DateTime(timezone=True), nullable=True)

    alerts = relationship("Alert", back_populates="edge_device")
    classroom = relationship(
        "Classroom",
        back_populates="devices",
        foreign_keys=[classroom_id],
        lazy="joined",
    )
    school = relationship(
        "School",
        foreign_keys=[school_id],
        lazy="joined",
        overlaps="devices",
    )

    @property
    def classroom_name(self) -> str | None:
        return self.classroom.name if self.classroom is not None else None

    @property
    def school_name(self) -> str | None:
        return self.school.name if self.school is not None else None

    @property
    def assignment_state(self) -> str:
        return "assigned" if self.classroom_id is not None else "unassigned"
