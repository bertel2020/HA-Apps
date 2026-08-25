"""Gemeinsame Laufzeitversion der Zeitarchiv-App."""

from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
APP_VERSION = VERSION_FILE.read_text(encoding="utf-8").strip()

if not APP_VERSION:
    raise RuntimeError(f"Leere Versionsdatei: {VERSION_FILE}")
