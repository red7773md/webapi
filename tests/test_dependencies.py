"""pyproject.toml and requirements.txt must name the same packages at the same versions.

Vercel installs from pyproject.toml. A package listed only in requirements.txt is missing there,
and the app then fails at import time.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_and_requirements_list_the_same_packages():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    from_pyproject = set(data["project"]["dependencies"])
    from_requirements = {
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert from_pyproject == from_requirements, (
        f"only in pyproject: {sorted(from_pyproject - from_requirements)}; "
        f"only in requirements: {sorted(from_requirements - from_pyproject)}"
    )


def test_a_missing_package_gives_a_readable_503(monkeypatch, tmp_path):
    import sys

    from fastapi.testclient import TestClient

    from app.entrypoint import build_entrypoint

    # Forget the cached app modules and make `import mcp` fail, as it would on a server without the package.
    for name in [n for n in list(sys.modules) if n == "app.main" or n.startswith("app.mcp") or n == "mcp" or n.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "mcp", None)

    env = {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "correct-horse-battery", "GEMINI_WEBUI_DATA": str(tmp_path)}
    with TestClient(build_entrypoint(env)) as client:
        resp = client.get("/api/chat")
        assert resp.status_code == 503
        assert "mcp" in resp.json()["error"]
