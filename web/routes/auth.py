"""
Auth REST endpoints — signup, login, and the Telegram chat-link handshake.

Signup always creates a `customer`. The first admin is seeded from env via
scripts.bootstrap_admin — there's intentionally no self-serve admin signup.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

import db

from ..auth import (
    chat_id_from_init_data,
    hash_password,
    issue_jwt,
    verify_init_data,
    verify_password,
)
from ..deps import current_user

router = APIRouter()


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int


class TelegramLinkBody(BaseModel):
    # Raw querystring produced by Telegram.WebApp.initData
    init_data: str


class TelegramLinkResponse(BaseModel):
    linked: bool
    chat_id: int


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupBody):
    if db.get_user_by_email(body.email):
        raise HTTPException(status_code=409, detail="email already registered")
    user_id = db.create_user(str(body.email), hash_password(body.password), role="customer")
    token, jti, expires_at = issue_jwt(user_id, "customer")
    db.create_session(jti, user_id, expires_at.isoformat())
    return TokenResponse(access_token=token, role="customer", user_id=user_id)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginBody):
    user = db.get_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token, jti, expires_at = issue_jwt(user["id"], user["role"])
    db.create_session(jti, user["id"], expires_at.isoformat())
    return TokenResponse(
        access_token=token,
        role=user["role"],
        user_id=user["id"],
    )


@router.post("/telegram-link", response_model=TelegramLinkResponse)
async def telegram_link(
    body: TelegramLinkBody,
    user: dict = Depends(current_user),
):
    """
    Link the caller's Telegram chat_id to their bot account.

    We do NOT trust a chat_id from the client body — it's extracted from the
    HMAC-verified initData that only Telegram could have produced.
    """
    parsed = verify_init_data(body.init_data)
    if not parsed:
        raise HTTPException(status_code=400, detail="invalid initData")
    chat_id = chat_id_from_init_data(parsed)
    if chat_id is None:
        raise HTTPException(status_code=400, detail="initData missing user.id")
    db.link_chat_to_user(chat_id, user["id"])
    return TelegramLinkResponse(linked=True, chat_id=chat_id)
