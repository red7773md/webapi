"""Runtime settings, read from environment variables.

Required everywhere:
    ADMIN_USERNAME, ADMIN_PASSWORD

Required on Vercel (Vercel sets VERCEL=1 at runtime):
    APP_SECRET_KEY            signs the browser session cookie
    BLOB_READ_WRITE_TOKEN     added automatically when a Blob store is connected

Optional:
    COOKIE_SECURE             "1" to force HTTPS-only session cookies (default "1" on Vercel)
    GEMINI_WEBUI_DATA         local folder for cookies and the generated secret (default ./data)
    APP_API_KEY               lets scripts call the chat, image and research routes without a login.
                              It is this app's own key, not a Gemini key. At least 24 characters.
    GEMINI_SYNC_KEY           lets the browser extension save the Gemini cookies (that one route only).
                              At least 24 characters. Set a different value from APP_API_KEY.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VERCEL_SCRATCH = Path("/tmp/gemini-webui")


class ConfigError(RuntimeError):
    """Required configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    admin_username: str
    admin_password: str
    secret_key: str
    cookie_secure: bool
    data_dir: Path
    blob_token: str | None = None
    on_vercel: bool = False
    api_key: str | None = None
    sync_key: str | None = None

    @property
    def storage_label(self) -> str:
        if self.blob_token:
            return "Vercel Blob (private store)"
        return f"local file ({self.data_dir})"


def _load_or_create_secret(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    path = data_dir / ".app_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_urlsafe(48)
    path.write_text(value, encoding="utf-8")
    os.chmod(path, 0o600)
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Read settings from `env` (defaults to os.environ). Raises ConfigError listing what is missing."""
    env = os.environ if env is None else env
    on_vercel = env.get("VERCEL") == "1"

    username = env.get("ADMIN_USERNAME", "").strip()
    password = env.get("ADMIN_PASSWORD", "")
    secret_key = env.get("APP_SECRET_KEY", "").strip()
    blob_token = env.get("BLOB_READ_WRITE_TOKEN", "").strip() or None
    api_key = env.get("APP_API_KEY", "").strip() or None
    sync_key = env.get("GEMINI_SYNC_KEY", "").strip() or None

    missing: list[str] = []
    if not username:
        missing.append("ADMIN_USERNAME")
    if not password:
        missing.append("ADMIN_PASSWORD")
    if on_vercel and not secret_key:
        missing.append("APP_SECRET_KEY")
    if on_vercel and not blob_token:
        missing.append("BLOB_READ_WRITE_TOKEN (connect a private Blob store to this project)")
    if missing:
        raise ConfigError("Missing configuration: " + ", ".join(missing))
    if api_key is not None and len(api_key) < 24:
        raise ConfigError("APP_API_KEY must be at least 24 characters.")
    if sync_key is not None and len(sync_key) < 24:
        raise ConfigError("GEMINI_SYNC_KEY must be at least 24 characters.")

    default_dir = VERCEL_SCRATCH if on_vercel else BASE_DIR / "data"
    data_dir = Path(env.get("GEMINI_WEBUI_DATA") or default_dir)
    if not secret_key:
        secret_key = _load_or_create_secret(data_dir)

    cookie_default = "1" if on_vercel else "0"
    return Settings(
        admin_username=username,
        admin_password=password,
        secret_key=secret_key,
        cookie_secure=env.get("COOKIE_SECURE", cookie_default) == "1",
        data_dir=data_dir,
        blob_token=blob_token,
        on_vercel=on_vercel,
        api_key=api_key,
        sync_key=sync_key,
    )
