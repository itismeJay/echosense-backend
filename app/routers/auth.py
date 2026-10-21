from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from jose import jwt, JWTError
from passlib.context import CryptContext
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.school import School
from app.schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
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
PASSWORD_CHANGE_REQUIRED = "password_change_required"
SCHOOL_INACTIVE = "school_inactive"


def belongs_to_inactive_school(user: User) -> bool:
    if is_global_admin(user):
        return False
    return user.school is not None and not user.school.is_active


async def resolve_new_user_scope(
    body: RegisterRequest,
    current_user: User,
    db: AsyncSession,
) -> tuple[UUID | None, bool]:
    if body.is_super_admin and not is_global_admin(current_user):
        raise HTTPException(status_code=403, detail="Super-admin access required")
    if is_global_admin(current_user):
        school_id = body.school_id
        if school_id is None and not body.is_super_admin:
            raise HTTPException(
                status_code=422,
                detail="school_id is required for users who are not super admins",
            )
    else:
        if current_user.school_id is None:
            raise HTTPException(status_code=403, detail="Administrator is not assigned to a school")
        if body.school_id is not None and body.school_id != current_user.school_id:
            raise HTTPException(status_code=403, detail="School access denied")
        school_id = current_user.school_id
    if school_id is not None:
        school = await db.get(School, school_id)
        if school is None:
            raise HTTPException(status_code=404, detail="School not found")
        if not school.is_active:
            raise HTTPException(status_code=409, detail="School is inactive")
    return school_id, body.is_super_admin


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Exact match first, then a case-insensitive match only when it is unambiguous."""

    exact = await db.execute(select(User).where(User.email == email))
    user = exact.scalar_one_or_none()
    if user is not None:
        return user
    folded = await db.execute(select(User).where(func.lower(User.email) == email.lower()).limit(2))
    matches = folded.scalars().all()
    return matches[0] if len(matches) == 1 else None


async def create_user_account(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession,
    current_user: User,
) -> UserOut:
    existing = await db.execute(
        select(User.id).where(func.lower(User.email) == body.email).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    school_id, is_super_admin = await resolve_new_user_scope(body, current_user, db)

    user = User(
        email=body.email,
        hashed_password=pwd_context.hash(body.password),
        role=body.role,
        school_id=school_id,
        is_super_admin=is_super_admin,
        must_change_password=True,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from exc
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


async def get_authenticated_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the bearer token without account-state gates; only for exempt routes."""

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")
    payload = decode_token(authorization.removeprefix("Bearer "))
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdigit():
        raise HTTPException(status_code=401, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == int(subject)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    # Also revokes tokens issued before the school was deactivated.
    if belongs_to_inactive_school(user):
        raise HTTPException(status_code=403, detail=SCHOOL_INACTIVE)
    return user


async def get_current_user(user: User = Depends(get_authenticated_user)) -> User:
    if user.must_change_password:
        raise HTTPException(status_code=403, detail=PASSWORD_CHANGE_REQUIRED)
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


async def require_super_admin(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not is_global_admin(current_user):
        await record_audit_event(
            db,
            request,
            AuditAction.PERMISSION_DENIED,
            AuditResource.SECURITY,
            AuditStatus.FAILURE,
            actor=current_user,
            target=request.url.path,
            description="User was denied access to a super-admin operation.",
            metadata={"method": request.method},
        )
        await db.commit()
        raise HTTPException(status_code=403, detail="Super-admin access required")
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
    user = await find_user_by_email(db, body.email)

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

    # Checked only after the password, so the school state is not disclosed to guessers.
    if belongs_to_inactive_school(user):
        await record_audit_event(
            db,
            request,
            AuditAction.LOGIN_FAILED,
            AuditResource.AUTHENTICATION,
            AuditStatus.FAILURE,
            actor=user,
            description="Login refused because the user's school is inactive.",
            metadata={"reason": SCHOOL_INACTIVE},
        )
        await db.commit()
        raise HTTPException(status_code=403, detail=SCHOOL_INACTIVE)

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
        must_change_password=user.must_change_password,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_authenticated_user)):
    # Exempt from the password-change gate so clients can render the change form.
    return UserOut.model_validate(current_user)


@router.post("/change-password", response_model=UserOut)
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_authenticated_user),
):
    if not pwd_context.verify(body.current_password, current_user.hashed_password):
        await record_audit_event(
            db,
            request,
            AuditAction.CHANGE_PASSWORD_FAILED,
            AuditResource.AUTHENTICATION,
            AuditStatus.FAILURE,
            actor=current_user,
            description="Password change rejected: current password did not match.",
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    was_temporary = current_user.must_change_password
    current_user.hashed_password = pwd_context.hash(body.new_password)
    current_user.must_change_password = False
    await record_audit_event(
        db,
        request,
        AuditAction.CHANGE_PASSWORD,
        AuditResource.AUTHENTICATION,
        AuditStatus.SUCCESS,
        actor=current_user,
        resource_id=current_user.id,
        description="User changed their password.",
        metadata={"was_temporary": was_temporary},
    )
    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return await create_user_account(request, body, db, current_user)
