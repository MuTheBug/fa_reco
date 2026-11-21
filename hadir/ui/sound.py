"""Short feedback sounds: Windows plays them with winsound, Linux with the sound server's player
(PipeWire, PulseAudio or ALSA, whichever is installed); elsewhere the system beep."""

from __future__ import annotations

import shutil
import subprocess
import sys

from ..paths import resource_dir

LINUX_PLAYERS = ("pw-play", "paplay", "aplay")
_player: list[str] | None = None


def linux_player() -> list[str]:
    """The command that plays a WAV file here ([] if none is installed)."""
    global _player
    if _player is None:
        _player = []
        for name in LINUX_PLAYERS:
            path = shutil.which(name)
            if path:
                _player = [path, "-q"] if name == "aplay" else [path]
                break
    return _player


def play(kind: str) -> None:
    path = resource_dir() / "sounds" / f"{kind}.wav"
    if sys.platform == "win32" and path.exists():
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return
    if sys.platform.startswith("linux") and path.exists() and linux_player():
        try:
            subprocess.Popen([*linux_player(), str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            return
        except OSError:
            pass
    try:
        from PySide6.QtWidgets import QApplication

        QApplication.beep()
    except Exception:
        pass
