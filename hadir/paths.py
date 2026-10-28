"""Where the program keeps its data (everything stays on this computer)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Hadir"


def app_root() -> Path:
    """Folder of the executable (frozen) or of the source checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    candidate = base / "hadir" / "resources"
    return candidate if candidate.is_dir() else Path(__file__).resolve().parent / "resources"


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Resolve the data folder.

    Order: HADIR_DATA_DIR, a ``data`` folder beside the program when a file
    named ``portable.txt`` exists there, then the machine-wide ProgramData
    folder on Windows (shared by every Windows account on the kiosk laptop),
    then the per-user folder.
    """
    env = os.environ.get("HADIR_DATA_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    root = app_root()
    if (root / "portable.txt").exists() and _writable(root / "data"):
        return root / "data"
    candidates = []
    if sys.platform == "win32":
        if os.environ.get("PROGRAMDATA"):
            candidates.append(Path(os.environ["PROGRAMDATA"]) / APP_NAME)
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / APP_NAME)
    else:
        candidates.append(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "hadir")
    for c in candidates:
        if _writable(c):
            return c
    fallback = Path.home() / APP_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def db_path() -> Path:
    return data_dir() / "hadir.db"


def snapshots_dir() -> Path:
    p = data_dir() / "snapshots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_dir(custom: str = "") -> Path:
    p = Path(custom) if custom else data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
