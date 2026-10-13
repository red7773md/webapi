"""Vercel entrypoint. Vercel serves this module's `app` for every route (see pyproject.toml)."""

from app.entrypoint import build_entrypoint

app = build_entrypoint()
