"""
FastAPI dependencies for authenticated routes.

`current_user` validates the bearer JWT AND checks server-side revocation
via the `sessions` table. `require_admin` layers on a role check.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

import db

from .auth import decode_jwt


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


async def current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_jwt(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        )
    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="malformed token")
    session = db.get_session(jti)
    if not session or session["revoked"]:
        raise HTTPException(status_code=401, detail="session revoked")
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="malformed token")
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="user gone")
    user["jti"] = jti
    return user


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return user
