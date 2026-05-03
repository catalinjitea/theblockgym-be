import os
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_admin
from app.core.email import send_password_reset_email
from app.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ADMIN_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdatePasswordRequest,
    UpdateProfileRequest,
    UserResponse,
)

router = APIRouter()

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"
COOKIE_NAME = "access_token"

# ── Helper: set auth cookie ───────────────────────────────────────────────────
def set_auth_cookie(response: Response, token: str, max_age_minutes: int) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="none" if IS_PRODUCTION else "lax",
        max_age=max_age_minutes * 60,
        path="/",
    )


# ── POST /auth/register ───────────────────────────────────────────────────────
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    if not body.terms_accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Termenii și condițiile trebuie acceptate.")
    if not body.privacy_accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Politica de confidențialitate trebuie acceptată.")

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")

    now = datetime.utcnow()
    user = User(
        first_name=body.first_name,
        last_name=body.last_name,
        email=body.email,
        phone_number=body.phone_number,
        age=body.age,
        hashed_password=hash_password(body.password),
        terms_accepted_at=now,
        privacy_accepted_at=now,
    )
    db.add(user)
    await db.flush()

    expire_minutes = ACCESS_TOKEN_EXPIRE_MINUTES
    token = create_access_token(subject=str(user.id), expires_delta=timedelta(minutes=expire_minutes))
    set_auth_cookie(response, token, max_age_minutes=expire_minutes)
    return user


# ── POST /auth/login ──────────────────────────────────────────────────────────
@router.post("/login", response_model=UserResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

    expire_minutes = ADMIN_TOKEN_EXPIRE_MINUTES if user.is_admin else ACCESS_TOKEN_EXPIRE_MINUTES
    token = create_access_token(subject=str(user.id), expires_delta=timedelta(minutes=expire_minutes))
    set_auth_cookie(response, token, max_age_minutes=expire_minutes)
    return user


# ── POST /auth/logout ─────────────────────────────────────────────────────────
@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        samesite="none" if IS_PRODUCTION else "lax",
        secure=IS_PRODUCTION,
    )
    return {"status": "logged out"}


# ── GET /auth/me ──────────────────────────────────────────────────────────────
@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


# ── PATCH /auth/me ────────────────────────────────────────────────────────────
@router.patch("/me", dependencies=[Depends(require_admin)], response_model=UserResponse)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.email and body.email != current_user.email:
        result = await db.execute(select(User).where(User.email == body.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use.")
        current_user.email = body.email

    if body.first_name:
        current_user.first_name = body.first_name
    if body.last_name:
        current_user.last_name = body.last_name
    if body.phone_number:
        current_user.phone_number = body.phone_number
    if body.age is not None:
        current_user.age = body.age

    return current_user


# ── PATCH /auth/me/password ───────────────────────────────────────────────────
@router.patch("/me/password", dependencies=[Depends(require_admin)])
async def update_password(
    body: UpdatePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Parola curentă este incorectă.")

    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parolele nu coincid.")

    current_user.hashed_password = hash_password(body.new_password)
    return {"status": "password updated"}


# ── POST /auth/forgot-password ────────────────────────────────────────────────
@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # Always return success to avoid leaking whether the email exists
    if user and user.is_active:
        token = secrets.token_urlsafe(32)
        user.password_reset_token = token
        user.password_reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        await db.flush()
        await send_password_reset_email(user.email, user.first_name, token)

    return {"status": "ok"}


# ── POST /auth/reset-password ─────────────────────────────────────────────────
@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parolele nu coincid.")

    result = await db.execute(select(User).where(User.password_reset_token == body.token))
    user = result.scalar_one_or_none()

    if not user or not user.password_reset_token_expires or user.password_reset_token_expires < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Link-ul de resetare este invalid sau a expirat.")

    user.hashed_password = hash_password(body.new_password)
    user.password_reset_token = None
    user.password_reset_token_expires = None
    return {"status": "password reset"}
