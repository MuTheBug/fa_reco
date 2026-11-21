"""Employees who live far away: their own Sunday (no lateness or absence until 10:00) and Thursday
(exit at 14:00); those who live near leave at 14:30 on Thursday."""

import sqlite3
from datetime import date, datetime, time

import pytest

from hadir.core import engine as E
from hadir.db.schema import MIGRATIONS
from hadir.db.seed import seed_defaults
from hadir.db.store import Database, StoreError

SUN, THU, MON = date(2026, 11, 22), date(2026, 11, 26), date(2026, 11, 23)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "f.db", clock=Clock(datetime(2026, 11, 22, 7, 0)))
    seed_defaults(d)
    return d


def day(db, far, d, *times):
    """A new employee who lives near (or far) and punched at ``times`` on ``d``."""
    eid = db.employees.save(dict(full_name="بعيد" if far else "قريب", far=far, hire_date="2026-01-01",
                                 group_id=db.employees.default_group()))
    for t in times:
        db.punches.add_face(eid, datetime.combine(d, time.fromisoformat(t)))
    [(_, [rec])] = db.compute([eid], d, d, now=datetime.combine(d, time(23, 0)))
    return rec


def test_sunday(db):
    r = day(db, True, SUN, "09:50", "14:50")
    assert (r.status, r.late) == (E.PRESENT, 0)  # neither late nor absent until 10:00
    r = day(db, True, SUN, "10:01", "14:50")
    assert r.status == E.ABSENT and "absent_late" in r.flags
    r = day(db, False, SUN, "08:40", "14:50")
    assert (r.status, r.late) == (E.PRESENT, 40)  # the usual rules
    assert day(db, False, SUN, "09:05", "14:50").status == E.ABSENT


def test_thursday(db):
    r = day(db, False, THU, "08:00", "14:30")
    assert (r.status, r.early, r.shift.end.time()) == (E.PRESENT, 0, time(14, 30))
    assert day(db, False, THU, "08:00", "14:20").early == 10  # exit punches from 14:15
    r = day(db, True, THU, "08:00", "14:00")
    assert (r.status, r.early, r.shift.end.time()) == (E.PRESENT, 0, time(14, 0))
    assert day(db, True, THU, "08:00", "13:50").early == 10  # exit punches from 13:45
    # the rest of the week is the same for everyone
    assert day(db, True, MON, "08:40", "14:45").late == 40
    assert day(db, True, MON, "08:00", "14:45").shift.end.time() == time(14, 45)


def test_the_schedule_keeps_its_far_days_and_checks_them(db):
    sid = db.scalar("SELECT id FROM schedules WHERE name = 'الدوام الرسمي'")
    s = db.schedules.get(sid)
    assert s.far_days[6].grace == s.far_days[6].absent == 120 and s.far_days[3].end == time(14, 0)
    assert s.days[3].end == time(14, 30) and s.days[6].grace is None
    with pytest.raises(StoreError, match="وقت الغياب"):
        db.schedules.save(dict(name="خاطئ", days=[(True, "08:00", "14:00", 60, 30)] * 7, grace_late=30,
                               absent_after=60))


def test_upgrade_from_1_4_moves_the_department_from_the_upgrade_day(tmp_path, monkeypatch):
    import hadir.db.store as store

    monkeypatch.setattr(store, "MIGRATIONS", MIGRATIONS[:5])
    old = Database(tmp_path / "v5.db", clock=Clock(datetime(2026, 11, 1, 9, 0)))
    stamp = "2026-10-01 08:00:00"  # the department's schedule as 1.4 made it: 08:00-14:45 Sunday to Thursday
    sid = old.x("INSERT INTO schedules (name, kind, grace_late, absent_after, exit_until, early_absent,"
                " missing_absent, exit_window, created_at) VALUES ('الدوام الرسمي', 'weekly', 30, 60, '17:00',"
                " 1, 1, 15, ?)", (stamp,)).lastrowid
    for i in range(7):
        work = i not in (4, 5)
        old.x("INSERT INTO schedule_days (schedule_id, day_index, workday, start_time, end_time) VALUES"
              " (?,?,?,?,?)", (sid, i, int(work), "08:00" if work else None, "14:45" if work else None))
    gid = old.x("INSERT INTO emp_groups (name, created_at) VALUES ('الموظفون الإداريون', ?)", (stamp,)).lastrowid
    old.x("INSERT INTO schedule_assignments (schedule_id, group_id, start_date, created_at) VALUES"
          " (?, ?, '2000-01-01', ?)", (sid, gid, stamp))
    eid = old.x("INSERT INTO employees (code, full_name, hire_date, created_at, updated_at) VALUES"
                " ('1', 'قديم', '2026-10-01', ?, ?)", (stamp, stamp)).lastrowid
    old.x("INSERT INTO employee_groups (employee_id, group_id, start_date) VALUES (?, ?, '2000-01-01')", (eid, gid))
    for ts in ("2026-11-19 08:00:00", "2026-11-19 14:40:00"):  # a Thursday before the upgrade
        old.x("INSERT INTO punches (employee_id, ts, original_ts, created_at) VALUES (?, ?, ?, ?)", (eid, ts, ts, ts))
    old.close()
    monkeypatch.setattr(store, "MIGRATIONS", MIGRATIONS)
    db = Database(tmp_path / "v5.db", clock=Clock(datetime(2026, 11, 21, 9, 0)))
    names = {r["name"]: r for r in db.schedules.list()}
    assert "الدوام الرسمي (حتى 2026/11/20)" in names and not names["الدوام الرسمي (حتى 2026/11/20)"]["active"]
    new = db.schedules.get(names["الدوام الرسمي"]["id"])
    assert new.far_days and new.days[3].end == time(14, 30) and new.exit_window == 15
    [(_, days)] = db.compute([eid], date(2026, 11, 19), date(2026, 11, 26))
    assert days[0].shift.end.time() == time(14, 45) and days[0].early == 5  # the old Thursday
    assert days[7].shift.end.time() == time(14, 30)  # the next Thursday
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'تطبيق قواعد الأحد والخميس'") == 1
    db.close()
    # a changed schedule is left alone
    conn = sqlite3.connect(tmp_path / "v5.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
