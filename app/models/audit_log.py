from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_email = Column(String(100), nullable=True)
    action = Column(String(100), nullable=False)
    module = Column(String(50), nullable=False)
    target = Column(String(100), nullable=True)
    performed_at = Column(DateTime, server_default=func.now())
