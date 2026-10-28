import sqlite3
from datetime import date, datetime

import numpy as np
import pytest

from hadir.core import engine as E
from hadir.db.seed import seed_defaults
from hadir.db.store import Database, StoreError


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def db(tmp_path):
    clock = Clock(datetime(2026, 10, 28, 15, 0))
    d = Database(tmp_path / "t.db", clock=clock)
    seed_defaults(d)
    d.users.create("admin", "المدير", "secret123", "admin")
    assert d.users.authenticate("admin", "secret123")
    d.clock_obj = clock
    return d


def add_emp(db, code="1001", name="أحمد علي", group=None):
    gid = group or db.scalar("SELECT id FROM emp_groups ORDER BY id")
    return db.employees.save(dict(code=code, full_name=name, group_id=gid, hire_date="2026-01-01"))


def test_seed_and_schema(db):
    assert db.schema_version == 1
    assert len(db.leaves.types()) == 6
    sched = db.schedules.all_engine()
    [s] = sched.values()
    assert s.shift_for(date(2026, 10, 25)) is not None  # Sunday
    assert s.shift_for(date(2026, 10, 30)) is None  # Friday
    seed_defaults(db)  # idempotent
    assert len(db.schedules.list()) == 1


def test_auth_and_failed_login_event(db):
    assert db.users.authenticate("admin", "wrong") is None
    assert db.events.list(kind="login_failed")
    with pytest.raises(StoreError):
        db.users.create("x", "", "123", "viewer")
    uid = db.users.create("viewer1", "مطلع", "viewpass", "viewer")
    assert db.users.authenticate("viewer1", "viewpass").role == "viewer"
    admin_id = db.scalar("SELECT id FROM users WHERE username='admin'")
    with pytest.raises(StoreError):
        db.users.update(admin_id, "المدير", "viewer", True)  # last admin
    assert uid


def test_settings_roundtrip(db):
    assert db.settings.get("recog.security") == "high"
    db.settings.set("org.name", "دائرة التجربة")
    assert db.settings.get("org.name") == "دائرة التجربة"
    assert db.settings.all()["org.name"] == "دائرة التجربة"


def test_employee_group_history(db):
    g2 = db.groups.save("المسائي")
    eid = add_emp(db)
    db.employees.set_group(eid, g2, "2026-10-26")
    hist = db.employees.group_history(eid)
    assert [(h["group_id"], h["start_date"], h["end_date"]) for h in hist][0] == (g2, "2026-10-26", None)
    assert hist[1]["end_date"] == "2026-10-25"
    [e] = db.load_employees([eid], date(2026, 10, 25), date(2026, 10, 26))
    assert e.group_on(date(2026, 10, 25)) != g2 and e.group_on(date(2026, 10, 26)) == g2
    with pytest.raises(StoreError):
        add_emp(db, code="1001", name="مكرر")


def test_punch_corrections_need_reason_and_are_append_only(db):
    eid = add_emp(db)
    pid = db.punches.add_face(eid, datetime(2026, 10, 25, 8, 20), score=0.7, liveness=0.99)
    with pytest.raises(StoreError):
        db.punches.edit(pid, datetime(2026, 10, 25, 8, 0), "auto", "")
    db.punches.edit(pid, datetime(2026, 10, 25, 8, 0), "auto", "عطل في الكاميرا صباحاً")
    db.punches.add_manual(eid, datetime(2026, 10, 25, 14, 0), "out", "نسي تسجيل الانصراف - بموافقة المدير")
    with pytest.raises(StoreError):
        db.punches.add_manual(eid, datetime(2026, 11, 25, 14, 0), "out", "سبب")  # future
    corr = db.punches.corrections(employee_id=eid)
    assert [c["action"] for c in corr] == ["edit", "add"]
    assert corr[0]["old_ts"] == "2026-10-25 08:20:00" and corr[0]["username"] == "admin"
    with pytest.raises(sqlite3.DatabaseError):
        db.x("UPDATE corrections SET reason = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        db.x("DELETE FROM corrections")
    with pytest.raises(sqlite3.DatabaseError):
        db.x("DELETE FROM punches")
    # original capture time is preserved
    assert db.punches.get(pid)["original_ts"] == "2026-10-25 08:20:00"
    [(e, days)] = db.compute([eid], date(2026, 10, 25), date(2026, 10, 25))
    r = days[0]
    assert r.status == E.PRESENT and r.late == 0 and r.check_out == datetime(2026, 10, 25, 14)
    assert {"corrected", "manual"} <= r.flags
    db.punches.delete(pid, "بصمة مكررة")
    [(e, days)] = db.compute([eid], date(2026, 10, 25), date(2026, 10, 25))
    assert days[0].check_in is None
    db.punches.restore(pid, "حذفت بالخطأ")
    assert len(db.punches.list(eid)) == 2


def test_leaves_clash_cancel_and_balance(db):
    eid = add_emp(db)
    annual = db.scalar("SELECT id FROM leave_types WHERE name='إجازة اعتيادية'")
    hourly = db.scalar("SELECT id FROM leave_types WHERE unit='hour'")
    lid = db.leaves.add(eid, annual, "2026-10-29", "2026-11-01", reference="أمر 12")
    with pytest.raises(StoreError):
        db.leaves.add(eid, annual, "2026-10-31", "2026-11-03")
    db.leaves.add(eid, hourly, "2026-10-26", None, "08:00", "10:00")
    with pytest.raises(StoreError):
        db.leaves.add(eid, hourly, "2026-10-26", None, "09:00", "11:00")
    bal = {b["name"]: b for b in db.leaves.balance(eid, 2026)}
    # 29 Oct (Thu) + 1 Nov (Sun); Fri/Sat are rest days
    assert bal["إجازة اعتيادية"]["used"] == 2 and bal["إجازة اعتيادية"]["remaining"] == 34
    assert bal["إجازة زمنية"]["used"] == pytest.approx(2.0)
    with pytest.raises(StoreError):
        db.leaves.cancel(lid, "")
    db.leaves.cancel(lid, "ألغيت بطلب الموظف")
    bal = {b["name"]: b for b in db.leaves.balance(eid, 2026)}
    assert bal["إجازة اعتيادية"]["used"] == 0


def test_holidays_and_schedule_assignment(db):
    gid = db.scalar("SELECT id FROM emp_groups ORDER BY id")
    eid = add_emp(db)
    db.holidays.save("عطلة", "2026-10-25", "2026-10-25")
    late = db.schedules.save(dict(name="رمضان", kind="weekly",
                                  days=[(True, "09:00", "13:00")] * 4 + [(False, None, None)] * 2
                                  + [(True, "09:00", "13:00")]))
    db.groups.assign_schedule(gid, late, "2026-10-26", "2026-10-27")
    db.punches.add_face(eid, datetime(2026, 10, 26, 9, 5))
    db.punches.add_face(eid, datetime(2026, 10, 26, 13, 0))
    db.punches.add_face(eid, datetime(2026, 10, 28, 9, 5))
    [(e, days)] = db.compute([eid], date(2026, 10, 25), date(2026, 10, 28))
    assert [d.status for d in days] == [E.HOLIDAY, E.PRESENT, E.ABSENT, E.PRESENT]
    assert days[1].late == 0 and days[1].schedule.name == "رمضان"
    assert days[3].late == 65 and days[3].schedule.name == "الدوام الرسمي"
    with pytest.raises(StoreError):
        db.schedules.delete(late)


def test_schedule_validation(db):
    with pytest.raises(StoreError):
        db.schedules.save(dict(name="ناقص", kind="weekly", days=[(True, "08:00", None)] * 7))
    with pytest.raises(StoreError):
        db.schedules.save(dict(name="دورة", kind="cycle", days=[(True, "08:00", "08:00")]))
    sid = db.schedules.save(dict(name="دورة", kind="cycle", cycle_anchor="2026-10-01",
                                 days=[(True, "08:00", "08:00"), (False, None, None), (False, None, None)]))
    s = db.schedules.get(sid)
    assert s.kind == "cycle" and len(s.days) == 3 and s.cycle_anchor == date(2026, 10, 1)


def test_faces_gallery(db):
    eid = add_emp(db)
    e2 = add_emp(db, "1002", "سارة")
    v = np.ones(4, np.float32) / 2
    db.faces.add_sample(eid, b"png", 0.9, "front", "enroll", {"m": v})
    db.faces.add_sample(e2, b"png", 0.9, "front", "enroll", {"m": -v})
    sids, eids, mat = db.faces.gallery("m", 4)
    assert list(eids) == [eid, e2] and mat.shape == (2, 4)
    assert len(db.faces.missing("other")) == 2
    db.employees.save(dict(code="1002", full_name="سارة", active=False), e2)
    assert list(db.faces.gallery("m", 4)[1]) == [eid]
    db.punches.add_face(eid, datetime(2026, 10, 25, 8))
    with pytest.raises(StoreError):
        db.employees.delete(eid)  # has attendance history
    e3 = add_emp(db, "1003", "بلا سجل")
    db.employees.delete(e3)
    assert db.employees.get(e3) is None


def test_templates_and_audit(db):
    tid = db.templates.save("تقريري", {"source": "daily", "columns": ["name"]})
    assert db.templates.get(tid)["source"] == "daily"
    db.templates.save("تقريري", {"source": "punches"})
    assert len(db.templates.list()) == 1
    actions = [r["action"] for r in db.audit_log.list()]
    assert "حفظ تقرير مخصّص" in actions and "تسجيل دخول" in actions


def test_backup(db, tmp_path):
    add_emp(db)
    target = tmp_path / "b.db"
    db.backup_to(target)
    other = Database(target)
    assert other.scalar("SELECT COUNT(*) FROM employees") == 1
