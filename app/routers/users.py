from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from app.database import get_db
from app.models.user import User
from app.schemas.user import UserOut, PushTokenRequest, RegisterRequest
from app.routers.auth import require_admin, get_current_user, pwd_context
from app.routers.audit_logs import log_action

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("/push-token")
async def save_push_token(
    body: PushTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.push_token = body.token
    await db.commit()
    return {"message": "Push token saved"}


@router.post("/", response_model=UserOut, status_code=201)
async def create_user(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=body.email,
        hashed_password=pwd_context.hash(body.password),
        role=body.role,
    )
    db.add(user)
    await db.flush()
    await log_action(db, current_user, "Created User", "Users", body.email)
    await db.commit()
    await db.refresh(user)
    return UserOut(id=str(user.id), email=user.email, role=user.role)


@router.get("/", response_model=List[UserOut])
async def get_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User))
    return [UserOut(id=str(u.id), email=u.email, role=u.role) for u in result.scalars().all()]


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await log_action(db, current_user, "Deleted User", "Users", user.email)
    await db.delete(user)
    await db.commit()
