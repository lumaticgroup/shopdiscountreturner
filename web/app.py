"""
FastAPI factory. Assembles routers, mounts static Mini App files, adds CORS
for the Telegram WebApp origin. Instantiated by bot.py::main() with a live
Telegram Bot handle so admin routes can trigger channel posts / reloads.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("web.app")

STATIC_DIR = Path(__file__).parent / "static"


def create_app(bot: Any | None = None) -> FastAPI:
    """
    Build the FastAPI application.

    `bot` is the python-telegram-bot `Bot` instance; admin routes stash it on
    app.state so they can send messages / post to channel without another
    round-trip.
    """
    app = FastAPI(
        title="Discount Bot Mini App",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.bot = bot

    # Telegram Mini Apps run inside t.me — permissive CORS is fine because
    # every write endpoint requires a JWT bearer + (for chat-link) a valid
    # HMAC-signed initData string from Telegram itself.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"status": "ok"})

    # Routers land here in later phases:
    #   app.include_router(auth.router, prefix="/api/auth")
    #   app.include_router(admin.router, prefix="/api/admin")
    #   app.include_router(me.router, prefix="/api")
    _mount_routers(app)

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    else:
        logger.warning("static dir %s missing — Mini App won't load", STATIC_DIR)

    return app


def _mount_routers(app: FastAPI) -> None:
    """
    Import routers lazily so this module doesn't blow up if a later phase
    hasn't been written yet. Each import is guarded — missing module means
    that route family is disabled, not a crash.
    """
    try:
        from .routes import auth as auth_routes
        app.include_router(auth_routes.router, prefix="/api/auth", tags=["auth"])
    except ImportError:
        logger.info("auth routes not available yet (phase 3)")

    try:
        from .routes import me as me_routes
        app.include_router(me_routes.router, prefix="/api", tags=["me"])
    except ImportError:
        logger.info("me routes not available yet (phase 3)")

    try:
        from .routes import admin as admin_routes
        app.include_router(admin_routes.router, prefix="/api/admin", tags=["admin"])
    except ImportError:
        logger.info("admin routes not available yet (phase 8)")
