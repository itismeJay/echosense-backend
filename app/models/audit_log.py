from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint(
            "status IS NULL OR status IN ('SUCCESS', 'FAILURE')",
            name="ck_audit_logs_status",
        ),
        Index("ix_audit_logs_occurred_at", "occurred_at"),
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_audit_logs_actor_email", "actor_email"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_resource", "resource"),
        Index("ix_audit_logs_status", "status"),
    )

    # Keep the existing database primary-key column so legacy rows retain IDs.
    id = Column("log_id", Integer, primary_key=True)
    occurred_at = Column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
    )
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_email = Column(String(320), nullable=True)
    actor_role = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False)
    resource = Column(String(100), nullable=False)
    resource_id = Column(String(100), nullable=True)
    target = Column(String(500), nullable=True)
    status = Column(String(10), nullable=True)
    description = Column(String(500), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)
    request_id = Column(String(36), nullable=True)
    metadata_json = Column(
        "metadata",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at = Column(DateTime(timezone=True), nullable=True, server_default=func.now())

    # Retained only so pre-migration rows preserve their original naive value.
    # New audit events never populate this column.
    legacy_performed_at = Column(DateTime, nullable=True)
