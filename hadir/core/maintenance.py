"""Backups, restore, clock-tamper detection and housekeeping."""

from __future__ import annotations

import shutil
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from ..db.store import Database
from ..paths import backups_dir, snapshots_dir
from .timeutil import parse_dt

BACKUP_PREFIX = "hadir-backup-"


def backup(db: Database, folder: str | Path | None = None, include_snapshots: bool = False,
           reason: str = "manual") -> Path:
    """Write a consistent copy of the database (and optionally evidence pictures) as a .zip."""
    folder = Path(folder) if folder else backups_dir(db.settings.get("backup.dir") or "")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = db.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{BACKUP_PREFIX}{stamp}.zip"
    tmp = folder / f".tmp-{stamp}.db"
    db.backup_to(tmp)
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "hadir.db")
            if include_snapshots:
                root = snapshots_dir()
                for f in root.rglob("*.jpg"):
                    z.write(f, "snapshots/" + f.relative_to(root).as_posix())
    finally:
        tmp.unlink(missing_ok=True)
    db.settings.set("backup.last", db.now_s(), audit=False)
    db.audit("نسخة احتياطية", "backup", None, {"file": str(target), "reason": reason})
    return target


def prune_backups(folder: Path, keep: int) -> int:
    files = sorted(folder.glob(f"{BACKUP_PREFIX}*.zip"))
    old = files[:-keep] if keep > 0 else []
    for f in old:
        f.unlink(missing_ok=True)
    return len(old)


def auto_backup_due(db: Database) -> bool:
    if not db.settings.get("backup.auto"):
        return False
    last = db.settings.get("backup.last")
    return not last or db.now() - parse_dt(last) > timedelta(hours=20)


def validate_backup(path: str | Path) -> str | None:
    """Return an Arabic error message, or None when the file is a usable backup."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            if "hadir.db" not in z.namelist():
                return "الملف ليس نسخة احتياطية لهذا البرنامج."
            data = z.read("hadir.db")
    except (zipfile.BadZipFile, OSError):
        return "تعذّرت قراءة الملف."
    if not data.startswith(b"SQLite format 3"):
        return "الملف تالف."
    probe = path.with_suffix(".probe.db")
    probe.write_bytes(data)
    try:
        con = sqlite3.connect(probe)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        if not ok:
            return "قاعدة البيانات في النسخة تالفة."
        if not {"employees", "punches", "corrections"} <= tables:
            return "الملف ليس نسخة احتياطية لهذا البرنامج."
    finally:
        probe.unlink(missing_ok=True)
    return None


def restore(backup_file: str | Path, db_file: Path) -> None:
    """Replace the database file with the one in the backup (the program must be restarted).
    The current database is kept beside it as a safety copy."""
    backup_file = Path(backup_file)
    msg = validate_backup(backup_file)
    if msg:
        raise ValueError(msg)
    safety = db_file.with_name(f"hadir-before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
    if db_file.exists():
        shutil.copy2(db_file, safety)
    with zipfile.ZipFile(backup_file) as z:
        db_file.write_bytes(z.read("hadir.db"))
        snaps = [n for n in z.namelist() if n.startswith("snapshots/")]
        root = snapshots_dir()
        for n in snaps:
            out = root / n[len("snapshots/"):]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
    for suffix in ("-wal", "-shm"):
        Path(str(db_file) + suffix).unlink(missing_ok=True)


class ClockGuard:
    """Detects the computer clock being moved (to fake punch times).

    * On start-up the clock must not be earlier than the last time the program
      saw it running.
    * While running, wall-clock time must advance like the monotonic clock.
    """

    TOLERANCE = timedelta(minutes=3)

    def __init__(self, db: Database, monotonic):
        self.db = db
        self.monotonic = monotonic
        self._ref_wall = None
        self._ref_mono = None

    def startup_check(self) -> str | None:
        last = self.db.settings.get("security.last_seen")
        now = self.db.now()
        msg = None
        if last and now < parse_dt(last) - self.TOLERANCE:
            msg = f"ساعة الجهاز ({now:%Y-%m-%d %H:%M}) أقدم من آخر تشغيل للبرنامج ({last[:16]})."
            self.db.events.add("clock_back", msg)
        self.heartbeat()
        return msg

    def heartbeat(self) -> str | None:
        now = self.db.now()
        mono = self.monotonic()
        msg = None
        if self._ref_wall is not None:
            drift = (now - self._ref_wall).total_seconds() - (mono - self._ref_mono)
            if abs(drift) > self.TOLERANCE.total_seconds():
                direction = "إلى الأمام" if drift > 0 else "إلى الخلف"
                msg = f"تغيّرت ساعة الجهاز {direction} بمقدار {abs(drift) / 60:.0f} دقيقة أثناء التشغيل."
                self.db.events.add("clock_jump", msg)
        self._ref_wall, self._ref_mono = now, mono
        last = self.db.settings.get("security.last_seen")
        if not last or now > parse_dt(last):
            self.db.settings.set("security.last_seen", self.db.now_s(), audit=False)
        return msg
