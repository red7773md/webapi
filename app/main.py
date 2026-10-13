"""FastAPI application: admin login, Gemini session management, static terminal UI.

Run locally:
    ADMIN_USERNAME=admin ADMIN_PASSWORD='change-me' \
      .venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, Settings, load_settings
from .cookie_store import CookieStore, parse_cookie_text
from .gemini_service import GeminiService
from .security import LoginThrottle, credentials_match

STATIC_DIR = BASE_DIR / "static"
SESSION_COOKIE = "gwui_session"
SESSION_MAX_AGE = 12 * 60 * 60  # seconds

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https:; "
    "media-src 'self' blob: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'"
)


class LoginIn(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=500)


class CookieIn(BaseModel):
    cookies: str = Field(max_length=20_000)


def create_app(
    settings: Settings | None = None,
    gemini_factory: Callable[[str, str | None], Any] | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    service = GeminiService(CookieStore(settings.data_dir / "gemini_cookies.json"), factory=gemini_factory)
    throttle = LoginThrottle()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await service.shutdown()

    app = FastAPI(
        title="Gemini Archive",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE,
        same_site="strict",
        https_only=settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def require_admin(request: Request) -> str:
        if not request.session.get("admin"):
            raise HTTPException(status_code=401, detail="Login required.")
        return str(request.session.get("user", ""))

    admin = Depends(require_admin)

    # ----- public ------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    # ----- auth --------------------------------------------------------------

    @app.post("/api/auth/login")
    async def login(body: LoginIn, request: Request) -> dict[str, Any]:
        key = request.client.host if request.client else "unknown"
        wait = throttle.retry_after(key)
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed attempts. Try again in {int(wait) + 1}s.",
                headers={"Retry-After": str(int(wait) + 1)},
            )
        if not credentials_match(
            body.username, body.password, settings.admin_username, settings.admin_password
        ):
            throttle.record_failure(key)
            raise HTTPException(status_code=401, detail="Invalid username or password.")

        throttle.record_success(key)
        request.session.clear()
        request.session["admin"] = True
        request.session["user"] = settings.admin_username
        return {"ok": True, "username": settings.admin_username}

    @app.post("/api/auth/logout")
    async def logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict[str, Any]:
        if request.session.get("admin"):
            return {"authenticated": True, "username": request.session.get("user")}
        return {"authenticated": False}

    # ----- session (Gemini cookies) -----------------------------------------

    @app.get("/api/session", dependencies=[admin])
    async def session_status() -> dict[str, Any]:
        return service.status()

    @app.post("/api/session/cookies", dependencies=[admin])
    async def save_cookies(body: CookieIn) -> Any:
        try:
            psid, psidts = parse_cookie_text(body.cookies)
        except ValueError as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})
        return await service.save_cookies(psid, psidts)

    @app.delete("/api/session/cookies", dependencies=[admin])
    async def clear_cookies() -> dict[str, Any]:
        return await service.clear_cookies()

    @app.post("/api/session/verify", dependencies=[admin])
    async def verify_cookies() -> dict[str, Any]:
        return await service.verify()

    app.state.gemini = service
    return app
