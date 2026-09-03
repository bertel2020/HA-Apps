"""Regressionstest für reproduzierbare Add-on-Laufzeitabhängigkeiten."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT


def test_runtime_requirements_are_fully_pinned_and_used_by_docker() -> None:
    locked = [
        line.strip()
        for line in (ADDON / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert locked
    assert all("==" in requirement for requirement in locked)
    assert not any(
        marker in requirement
        for requirement in locked
        for marker in (">=", "<=", "~=", ">", "<")
    )

    source = (ADDON / "requirements.in").read_text(encoding="utf-8")
    for direct in ("fastapi", "uvicorn", "pyarrow", "jinja2", "python-multipart"):
        assert direct in source

    dockerfile = (ADDON / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt ." in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile
