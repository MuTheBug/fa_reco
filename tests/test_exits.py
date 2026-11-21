"""Leaving during the working day from the administration, and one exit a day."""

from datetime import date, datetime

import pytest

from hadir.core import engine as E
from hadir.db.seed import seed_defaults
from hadir.db.store import Database, StoreError
from hadir.kiosk_logic import KioskLogic, exit_state, record_exit


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "x.db", clock=Clock(datetime(2026, 10, 25, 8, 0)))  # a Sunday
    seed_defaults(d)
    d.users.enter()
    return d


def add(db, name):
    return db.employees.save(dict(full_name=name, job_title="موظف", group_id=db.employees.default_group(),
                                  hire_date="2026-01-01"))


def day(db, eid, d=date(2026, 10, 25)):
    [(_, [rec])] = db.compute([eid], d, d, now=datetime(2026, 10, 25, 17, 30))
    return rec


def leave_type(db, unit="hour"):
    return db.scalar("SELECT id FROM leave_types WHERE unit = ? ORDER BY id", (unit,))


def arrive(db, eid, hhmm="08:00"):
    db.clock.now = datetime.fromisoformat(f"2026-10-25 {hhmm}")
    assert KioskLogic(db).register(eid, db.clock.now).kind == "punched"


def test_leaving_on_an_hourly_leave_until_the_end_of_the_day(db):
    eid = add(db, "سعد")
    arrive(db, eid)
    db.clock.now = datetime(2026, 10, 25, 11, 30)
    record_exit(db, eid, db.clock.now, "hourly", leave_type(db), "مراجعة طبية")
    [lv] = db.leaves.list(eid)
    assert (lv["start_time"], lv["end_time"], lv["note"]) == ("11:30", "14:45", "مراجعة طبية")
    rec = day(db, eid)
    assert rec.status == E.PRESENT and rec.check_out == datetime(2026, 10, 25, 11, 30)
    assert (rec.early, rec.late) == (0, 0)
    [p] = [p for p in db.punches.list(eid) if p["source"] == "manual"]
    assert p["kind"] == "out"
    reason = db.scalar("SELECT reason FROM corrections WHERE punch_id = ?", (p["id"],))
    assert reason == "تسجيل خروج أثناء الدوام: إجازة ساعية حتى نهاية الدوام — مراجعة طبية"
    # one exit a day: neither the kiosk nor the button records another
    db.clock.now = datetime(2026, 10, 25, 14, 50)
    assert KioskLogic(db).register(eid, db.clock.now).kind == "left"
    with pytest.raises(StoreError, match="لا يُسجَّل خروج ثانٍ"):
        record_exit(db, eid, db.clock.now, "plain")


def test_leaving_on_a_day_of_leave_or_without_leave(db):
    on_leave, early = add(db, "منى"), add(db, "كريم")
    arrive(db, on_leave)
    arrive(db, early)
    db.clock.now = datetime(2026, 10, 25, 10, 0)
    record_exit(db, on_leave, db.clock.now, "daily", leave_type(db, "day"))
    rec = day(db, on_leave)
    assert rec.status == E.LEAVE and rec.leave.type.name == "إجازة اعتيادية"
    record_exit(db, early, db.clock.now, "plain")
    rec = day(db, early)
    assert rec.status == E.ABSENT and "absent_early" in rec.flags  # the department's rules decide


def test_who_cannot_be_given_an_exit(db):
    absent = add(db, "غائب")
    assert exit_state(db, absent, datetime(2026, 10, 25, 10, 0))[1] == "لم يسجّل حضوراً اليوم."
    with pytest.raises(StoreError):
        record_exit(db, absent, datetime(2026, 10, 25, 10, 0), "plain")
    friday = datetime(2026, 10, 30, 10, 0)
    db.clock.now = friday
    assert exit_state(db, absent, friday)[1] == "لا دوام له اليوم."
    weekly = db.scalar("SELECT id FROM emp_groups WHERE name = 'حرس النوبات الأسبوعية'")
    guard = db.employees.save(dict(full_name="حارس", group_id=weekly, hire_date="2026-01-01",
                                   cycle_start="2026-10-25"))
    db.clock.now = datetime(2026, 10, 25, 8, 0)
    KioskLogic(db).register(guard, db.clock.now)
    assert "النوبة المتصلة" in exit_state(db, guard, datetime(2026, 10, 27, 12, 0))[1]
    nobody = add(db, "متأخر عن الخروج")
    arrive(db, nobody, "08:10")
    assert "قبل حضوره" in exit_state(db, nobody, datetime(2026, 10, 25, 8, 5))[1]


def test_the_exit_dialog_on_the_dashboard(qapp, tmp_path, monkeypatch):
    from hadir.ui.main_window import AppContext, MainWindow
    from hadir.ui.pages import dashboard
    from hadir.ui.selfcheck import IdleFaceWorker, NoCamera

    monkeypatch.setenv("HADIR_DATA_DIR", str(tmp_path))
    d = Database(tmp_path / "ui.db", clock=Clock(datetime(2026, 10, 25, 8, 0)))
    seed_defaults(d)
    eid = add(d, "نادية")
    KioskLogic(d).register(eid, d.clock.now)
    d.clock.now = datetime(2026, 10, 25, 12, 15)
    win = MainWindow(AppContext(d, NoCamera(), IdleFaceWorker()), kiosk_first=False)
    try:
        win.start()
        page = win.admin.pages["dashboard"]
        errors = []
        monkeypatch.setattr(dashboard, "error", lambda parent, text: errors.append(text))
        page._record_exit()
        assert errors == ["اختر من الجدول الموظف الذي يغادر."]
        rec, why = exit_state(d, eid, d.now())
        dlg = dashboard.ExitDialog(d, eid, "نادية", rec, page)
        assert dlg.options["hourly"].isChecked() and dlg.time.value().strftime("%H:%M") == "12:15"
        dlg.note.setText("مراجعة دائرة")
        dlg.save()
        assert dlg.result() == dashboard.QDialog.Accepted
        assert day(d, eid).check_out == datetime(2026, 10, 25, 12, 15)
    finally:
        win._allow_close = True
        win.close()
