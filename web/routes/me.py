"""
Endpoints scoped to the currently authenticated user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

import db

from ..deps import current_user

router = APIRouter()


class MeResponse(BaseModel):
    id: int
    email: str
    role: str
    linked_chat: bool


@router.get("/me", response_model=MeResponse)
async def me(user: dict = Depends(current_user)):
    return MeResponse(
        id=user["id"],
        email=user["email"],
        role=user["role"],
        linked_chat=_has_linked_chat(user["id"]),
    )


@router.post("/auth/logout")
async def logout(user: dict = Depends(current_user)):
    db.revoke_session(user["jti"])
    return {"logged_out": True}


def _has_linked_chat(user_id: int) -> bool:
    # Small helper — one row query. If we grow more me-scoped fields we can
    # move this to the facade.
    from db.sqlite_backend import _conn  # local import to avoid public API change
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM chat_links WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()
        return r is not None
