from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class EdgeDevice(Base):
    __tablename__ = "edge_devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    device_code = Column(String(100), unique=True, nullable=False)
    display_name = Column(String(200), nullable=False)
    classroom_name = Column(String(200), nullable=False)
    school_name = Column(String(200), nullable=True)
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

    alerts = relationship("Alert", back_populates="edge_device")
