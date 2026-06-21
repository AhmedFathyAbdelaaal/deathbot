"""
Central configuration for the web-platform half of the bot.

All values come from environment variables (see ``deathbot-env-vars.md`` and
``.env.example``). Imported lazily *after* ``load_dotenv()`` runs in ``bot.py``
so the local ``.env`` is in effect; in Coolify these are real env vars.
"""

import os


def _normalize_db_url(url: str) -> str:
    """SQLAlchemy's async engine needs the ``postgresql+asyncpg://`` scheme.
    Coolify hands out a bare ``postgres://`` (or ``postgresql://``) URL, so
    rewrite it here rather than asking the operator to edit the env var."""
    if not url:
        return url
    for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", ""))
API_PORT = int(os.getenv("API_PORT", "8000"))
PIN_LENGTH = int(os.getenv("PIN_LENGTH", "4"))
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "").rstrip("/")
CORS_ALLOWED_ORIGIN = os.getenv("CORS_ALLOWED_ORIGIN", "").rstrip("/")
UPLOADS_PATH = os.getenv("UPLOADS_PATH", "/data/deathbot/uploads")
UPLOAD_MAX_BYTES = int(os.getenv("UPLOAD_MAX_BYTES", str(250 * 1024 * 1024)))
