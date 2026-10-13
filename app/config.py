"""Runtime settings, read from environment variables.

Required:
    ADMIN_USERNAME, ADMIN_PASSWORD

Optional:
    APP_SECRET_KEY   Signs the browser session cookie. If unset, a random key is
                     generated once and stored in DATA_DIR/.app_secret (chmod 600).
    COOKIE_SECURE    "1" to mark the session cookie HTTPS-only (use behind TLS).
    GEMINI_WEBUI_DATA  Directory for local state (cookies, saved outputs).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    admin_username: str
    admin_password: str
    secret_key: str
    cookie_secure: bool
    data_dir: Path


def _load_or_create_secret(data_dir: Path) -> str:
    path = data_dir / ".app_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)
    return value


def load_settings() -> Settings:
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD before starting the server.")

    data_dir = Path(os.environ.get("GEMINI_WEBUI_DATA", BASE_DIR / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)

    secret_key = os.environ.get("APP_SECRET_KEY") or _load_or_create_secret(data_dir)
    return Settings(
        admin_username=username,
        admin_password=password,
        secret_key=secret_key,
        cookie_secure=os.environ.get("COOKIE_SECURE", "0") == "1",
        data_dir=data_dir,
    )
