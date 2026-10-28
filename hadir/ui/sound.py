"""Short feedback sounds (Windows: winsound, elsewhere: system beep)."""

from __future__ import annotations

import sys

from ..paths import resource_dir


def play(kind: str) -> None:
    path = resource_dir() / "sounds" / f"{kind}.wav"
    if sys.platform == "win32" and path.exists():
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return
    try:
        from PySide6.QtWidgets import QApplication

        QApplication.beep()
    except Exception:
        pass
