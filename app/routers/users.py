from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.database import get_db
from app.models.user import User
from app.schemas.user import UserOut
from app.routers.auth import require_admin

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/", response_model=List[UserOut])
async def get_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User))
    return [UserOut(id=str(u.id), email=u.email, role=u.role) for u in result.scalars().all()]
