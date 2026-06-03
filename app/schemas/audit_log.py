from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class AuditLogOut(BaseModel):
    log_id: int
    user_id: Optional[int] = None
    actor_email: Optional[str] = None
    action: str
    module: str
    target: Optional[str] = None
    performed_at: datetime

    class Config:
        from_attributes = True
