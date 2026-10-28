"""Bundled Arabic fonts (Noto Sans Arabic, SIL Open Font License)."""

from __future__ import annotations

from .paths import resource_dir

_done = False


def register_fonts() -> str:
    """Register the bundled fonts with Qt; returns the family name to use."""
    global _done
    from PySide6.QtGui import QFontDatabase

    family = "Noto Sans Arabic"
    if not _done:
        for f in sorted((resource_dir() / "fonts").glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(f))
        _done = True
    return family
