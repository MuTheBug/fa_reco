import zipfile
from datetime import date, datetime, timedelta

import pytest

from hadir.core import maintenance
from hadir.db.store import Database
from hadir.demo import demo_database


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HADIR_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_backup_validate_restore(env):
    db_file = env / "data" / "hadir.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = demo_database(db_file, date(2026, 10, 28), employees=5, days=10)
    n = db.scalar("SELECT COUNT(*) FROM punches")
    target = maintenance.backup(db, env / "b")
    assert target.exists() and zipfile.is_zipfile(target)
    assert maintenance.validate_backup(target) is None
    assert db.settings.get("backup.last")
    # damage the live database, then restore
    db.x("UPDATE employees SET full_name = 'X'")
    db.close()
    maintenance.restore(target, db_file)
    db2 = Database(db_file)
    assert db2.scalar("SELECT COUNT(*) FROM punches") == n
    assert db2.scalar("SELECT COUNT(*) FROM employees WHERE full_name = 'X'") == 0
    assert list(db_file.parent.glob("hadir-before-restore-*.db"))


def test_validate_rejects_foreign_files(env):
    bad = env / "x.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hadir.db", b"not a database")
    assert maintenance.validate_backup(bad)
    other = env / "y.zip"
    other.write_bytes(b"junk")
    assert maintenance.validate_backup(other)


def test_prune_backups(env):
    folder = env / "b"
    folder.mkdir()
    for i in range(5):
        (folder / f"{maintenance.BACKUP_PREFIX}2026010{i}-000000.zip").write_bytes(b"x")
    assert maintenance.prune_backups(folder, 2) == 3
    assert len(list(folder.glob("*.zip"))) == 2


def test_auto_backup_due(env):
    clock = Clock(datetime(2026, 10, 28, 9))
    db = Database(env / "a.db", clock=clock)
    assert maintenance.auto_backup_due(db)
    db.settings.set("backup.last", "2026-10-28 08:00:00")
    assert not maintenance.auto_backup_due(db)
    clock.now += timedelta(days=1)
    assert maintenance.auto_backup_due(db)


def test_clock_guard_detects_rollback_and_jump(env):
    clock = Clock(datetime(2026, 10, 28, 9))
    mono = [1000.0]
    db = Database(env / "c.db", clock=clock)
    guard = maintenance.ClockGuard(db, lambda: mono[0])
    assert guard.startup_check() is None
    mono[0] += 60
    clock.now += timedelta(minutes=1)
    assert guard.heartbeat() is None
    # someone moves the clock back 2 hours while the program runs
    mono[0] += 60
    clock.now -= timedelta(hours=2)
    assert guard.heartbeat()
    assert db.events.list(kind="clock_jump")
    # restart with the clock still behind the last time seen
    guard2 = maintenance.ClockGuard(db, lambda: 0.0)
    clock.now = datetime(2026, 10, 28, 8)
    db.settings.set("security.last_seen", "2026-10-28 09:02:00", audit=False)
    assert guard2.startup_check()
    assert db.events.list(kind="clock_back")


def test_qprinter_pdf_path(qapp, env):
    from PySide6.QtPrintSupport import QPrinter

    from hadir.reports import builtin
    from hadir.reports.render import page_layout, paint_on

    db = demo_database(env / "p.db", date(2026, 10, 28), employees=6, days=10)
    rep = builtin.build(db, "timesheet", {"start": date(2026, 10, 18), "end": date(2026, 10, 27)})
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    out = env / "printed.pdf"
    printer.setOutputFileName(str(out))
    printer.setPageLayout(page_layout(rep))
    pages = paint_on(printer, rep)
    assert pages == 6  # one page per employee
    assert out.read_bytes()[:4] == b"%PDF"
