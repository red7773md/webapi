"""Builds the ASGI app for local runs and for Vercel.

If required configuration is missing, this returns a small app that answers 503
and names the missing settings. That shows a clear message on the deployment
instead of a crashed function.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import ConfigError, load_settings


def build_entrypoint(env: Mapping[str, str] | None = None) -> FastAPI:
    try:
        # Imported here so a missing package gives a readable 503, not a crash with no message.
        from .main import create_app

        settings = load_settings(env)
    except ConfigError as exc:
        return _not_configured_app(str(exc))
    except ImportError as exc:
        missing = exc.name or str(exc)
        return _not_configured_app(
            f"The server is missing the package {missing}. It must be listed in pyproject.toml and requirements.txt."
        )
    return create_app(settings)


def _not_configured_app(message: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def not_configured(path: str = "") -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "Server is not configured yet.", "error": message},
        )

    return app
