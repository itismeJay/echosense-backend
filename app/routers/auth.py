from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.school import School
from app.schemas.user import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.audit import (
    AuditAction,
    AuditResource,
    AuditStatus,
    record_audit_event,
)
from app.services.school_access import is_global_admin

router = APIRouter(prefix="/auth", tags=["Auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
ALERT_REVIEWER_ROLES = frozenset({"admin", "staff", "counselor"})


async def resolve_new_user_scope(
    body: RegisterRequest,
    current_user: User,
    db: AsyncSession,
) -> tuple[UUID | None, bool]:
    if body.is_super_admin and not is_global_admin(current_user):
        raise HTTPException(status_code=403, detail="Super-admin access required")
    if is_global_admin(current_user):
        school_id = body.school_id
    else:
        if current_user.school_id is None:
            raise HTTPException(status_code=403, detail="Administrator is not assigned to a school")
        if body.school_id is not None and body.school_id != current_user.school_id:
            raise HTTPException(status_code=403, detail="School access denied")
        school_id = current_user.school_id
    if school_id is not None and await db.get(School, school_id) is None:
        raise HTTPException(status_code=404, detail="School not found")
    return school_id, body.is_super_admin


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")
    payload = decode_token(authorization.removeprefix("Bearer "))
    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


async def require_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    if current_user.role != "admin":
        await record_audit_event(
            db,
            request,
            AuditAction.PERMISSION_DENIED,
            AuditResource.SECURITY,
            AuditStatus.FAILURE,
            actor=current_user,
            target=request.url.path,
            description="User was denied access to an administrative operation.",
            metadata={"method": request.method},
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


async def require_alert_reviewer(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    if current_user.role not in ALERT_REVIEWER_ROLES:
        await record_audit_event(
            db,
            request,
            AuditAction.PERMISSION_DENIED,
            AuditResource.SECURITY,
            AuditStatus.FAILURE,
            actor=current_user,
            target=request.url.path,
            description="User was denied access to alert evidence.",
            metadata={"method": request.method},
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="Alert reviewer access required")
    return current_user


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not pwd_context.verify(body.password, user.hashed_password):
        await record_audit_event(
            db,
            request,
            AuditAction.LOGIN_FAILED,
            AuditResource.AUTHENTICATION,
            AuditStatus.FAILURE,
            actor_email=body.email,
            description="Login attempt failed.",
        )
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_token(user)
    await record_audit_event(
        db,
        request,
        AuditAction.LOGIN,
        AuditResource.AUTHENTICATION,
        AuditStatus.SUCCESS,
        actor=user,
        description="User signed in successfully.",
    )
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    school_id, is_super_admin = await resolve_new_user_scope(body, current_user, db)

    user = User(
        email=body.email,
        hashed_password=pwd_context.hash(body.password),
        role=body.role,
        school_id=school_id,
        is_super_admin=is_super_admin,
    )
    db.add(user)
    await db.flush()
    await record_audit_event(
        db,
        request,
        AuditAction.CREATE_USER,
        AuditResource.USER,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=user.id,
        target=user.email,
        description="Administrator created a user account.",
        metadata={"role": user.role},
        school_id=user.school_id,
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
