"""
FastAPI application factory.

Runs in the same process and event loop as the discord.py client (see
``bot.py``). The bot instance is stashed on ``app.state.bot`` so route handlers
can reach the shared service layer and live playback state. Routers for queue,
library, and playlists are mounted here in later Phase 2 steps.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import config
from api.routers import auth, queue
from db.base import get_sessionmaker

logger = logging.getLogger(__name__)


def create_app(bot) -> FastAPI:
    app = FastAPI(title="Death Bot API", version="0.1.0")
    app.state.bot = bot

    # The API is public, so CORS is locked to the one frontend origin — never
    # wildcard. If the origin is unset we leave CORS off (all cross-origin
    # requests blocked) rather than silently opening up.
    if config.CORS_ALLOWED_ORIGIN:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[config.CORS_ALLOWED_ORIGIN],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        logger.info(f"CORS locked to origin: {config.CORS_ALLOWED_ORIGIN}")
    else:
        logger.warning("CORS_ALLOWED_ORIGIN not set — cross-origin requests will be blocked")

    @app.get("/health")
    async def health():
        """Liveness + dependency check. Used by the operator to confirm the API
        is up at api.deathbot.captionato.tech and can reach Postgres."""
        db_ok = False
        sm = get_sessionmaker()
        if sm is not None:
            try:
                async with sm() as session:
                    await session.execute(text("SELECT 1"))
                db_ok = True
            except Exception as e:
                logger.error(f"Health check DB ping failed: {e}")
        return {
            "status": "ok",
            "discord_ready": bot.is_ready(),
            "db": db_ok,
        }

    app.include_router(auth.router)
    app.include_router(queue.router)
    return app
