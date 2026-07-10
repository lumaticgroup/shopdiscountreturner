"""
Admin-only endpoints for CRUD on dynamic stores, live scraper-registry
reload, and manual channel firehose flush ("publish").

All routes here depend on `require_admin` — non-admins get 403.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

import channel
import db
from scraper.stores import refresh_dynamic_stores

from ..deps import require_admin

router = APIRouter()
logger = logging.getLogger("web.admin")


class StoreConfig(BaseModel):
    """
    Payload for `dynamic_stores.config_json`. Kept loose intentionally —
    the shape is a JSONPath-driven adapter (see GenericAPIStore), and
    over-validating here would force us to keep two schemas in sync.
    """
    list_url: str
    method: str = "GET"
    response_items_path: str = "$[*]"
    fields: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] = Field(default_factory=dict)
    pagination: Optional[dict[str, Any]] = None


class StoreUpsertBody(BaseModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    display_name: str = Field(min_length=1, max_length=64)
    flow_type: Literal["api", "header"]
    base_url: str = Field(min_length=1)
    currency: str = Field(min_length=1, max_length=8)
    storefront_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    config: StoreConfig
    enabled: bool = True


class StoreOut(BaseModel):
    code: str
    display_name: str
    flow_type: str
    base_url: str
    currency: str
    storefront_code: str
    enabled: bool
    config: dict


class ReloadResponse(BaseModel):
    loaded: int


class PublishResponse(BaseModel):
    posted: int


@router.get("/stores", response_model=list[StoreOut])
async def list_stores(_: dict = Depends(require_admin)):
    return [_row_to_out(r) for r in db.list_dynamic_stores(enabled_only=False)]


@router.post("/stores", response_model=StoreOut, status_code=status.HTTP_200_OK)
async def upsert_store(
    body: StoreUpsertBody,
    admin: dict = Depends(require_admin),
):
    db.upsert_dynamic_store(
        code=body.code,
        display_name=body.display_name,
        flow_type=body.flow_type,
        base_url=body.base_url,
        currency=body.currency,
        storefront_code=body.storefront_code,
        config_json=json.dumps(body.config.model_dump(exclude_none=True)),
        enabled=body.enabled,
        created_by=admin["id"],
    )
    row = db.get_dynamic_store(body.code)
    if row is None:
        raise HTTPException(status_code=500, detail="upsert did not persist")
    # Immediately reload so the new store is live without a second click.
    try:
        refresh_dynamic_stores()
    except Exception:
        logger.exception("refresh_dynamic_stores failed after upsert")
    return _row_to_out(row)


@router.delete("/stores/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(code: str, _: dict = Depends(require_admin)):
    if db.get_dynamic_store(code) is None:
        raise HTTPException(status_code=404, detail="not found")
    db.delete_dynamic_store(code)
    try:
        refresh_dynamic_stores()
    except Exception:
        logger.exception("refresh_dynamic_stores failed after delete")
    return None


@router.post("/reload", response_model=ReloadResponse)
async def reload_registry(_: dict = Depends(require_admin)):
    loaded = refresh_dynamic_stores()
    return ReloadResponse(loaded=loaded)


@router.post("/publish", response_model=PublishResponse)
async def publish_now(request: Request, _: dict = Depends(require_admin)):
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        raise HTTPException(
            status_code=503,
            detail="bot is not attached to the FastAPI app",
        )
    posted = await channel.post_qualifying_deals(bot)
    return PublishResponse(posted=posted)


def _row_to_out(row: dict) -> StoreOut:
    try:
        config = json.loads(row.get("config_json") or "{}")
    except json.JSONDecodeError:
        config = {}
    return StoreOut(
        code=row["code"],
        display_name=row["display_name"],
        flow_type=row["flow_type"],
        base_url=row["base_url"],
        currency=row["currency"],
        storefront_code=row["storefront_code"],
        enabled=bool(row.get("enabled", 1)),
        config=config,
    )
