"""Exporting the employees and the punches."""

import csv
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from hadir.db.seed import seed_defaults
from hadir.db.store import Database
from hadir.kiosk_logic import KioskLogic, record_exit
from hadir.reports import exports as X


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "e.db", clock=Clock(datetime(2026, 11, 22, 8, 0)))  # a Sunday
    seed_defaults(d)
    d.users.enter()
    return d


def test_employees_to_excel_and_csv(db, tmp_path):
    db.employees.save(dict(full_name="سلمى", phone="07701112233", job_title="محاسبة", far=True,
                           group_id=db.employees.default_group()))
    xlsx, text = str(tmp_path / "m.xlsx"), str(tmp_path / "m.csv")
    assert X.export_employees(db, xlsx) == 1
    ws = load_workbook(xlsx).active
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0]) == X.EMPLOYEE_HEADERS and ws.sheet_view.rightToLeft
    record = dict(zip(rows[0], rows[1]))
    assert (record["الاسم"], record["رقم الهاتف"], record["السكن"]) == ("سلمى", "07701112233", "بعيد")
    assert record["المجموعة"] == "الموظفون الإداريون" and record["الحالة"] == "فعّال"
    X.export_employees(db, text)
    raw = open(text, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel then reads the Arabic correctly
    assert list(csv.reader(raw.decode("utf-8-sig").splitlines()))[1][1] == "سلمى"


def test_punches_with_what_they_counted_as(db, tmp_path):
    eid = db.employees.save(dict(full_name="عمار", group_id=db.employees.default_group()))
    logic = KioskLogic(db)
    logic.register(eid, datetime(2026, 11, 22, 8, 5))
    db.clock.now = datetime(2026, 11, 22, 12, 0)
    hourly = db.scalar("SELECT id FROM leave_types WHERE unit = 'hour'")
    record_exit(db, eid, db.clock.now, "hourly", hourly, "مراجعة")
    db.punches.add_face(eid, datetime(2026, 11, 22, 15, 0))  # passing the camera later (old data)
    db.clock.now = datetime(2026, 11, 23, 9, 0)
    path = str(tmp_path / "p.xlsx")
    assert X.export_punches(db, date(2026, 11, 1), date(2026, 11, 30), path) == 3
    rows = list(load_workbook(path).active.iter_rows(values_only=True))
    assert list(rows[0]) == X.PUNCH_HEADERS
    got = [(r[5][:5], r[6], r[7], r[8] or "") for r in rows[1:]]  # an empty cell reads back as None
    assert got == [("08:05", "حضور", "الوجه", ""),
                   ("12:00", "انصراف", "إدخال يدوي",
                    "تسجيل خروج أثناء الدوام: إجازة ساعية حتى نهاية الدوام — مراجعة"),
                   ("15:00", "لم تُحتسب", "الوجه", "")]
    assert rows[1][3] == "2026-11-22" and rows[1][4] == "الأحد"
    assert X.export_punches(db, date(2026, 11, 23), date(2026, 11, 30), str(tmp_path / "none.csv")) == 0


def test_an_exported_file_can_be_imported_again(qapp, db, tmp_path, monkeypatch):
    from hadir.ui.main_window import AppContext
    from hadir.ui.pages import employees
    from hadir.ui.selfcheck import IdleFaceWorker, NoCamera

    eid = db.employees.save(dict(full_name="حيدر", group_id=db.employees.default_group()))
    path = str(tmp_path / "all.xlsx")
    X.export_employees(db, path)
    wb = load_workbook(path)
    ws = wb.active
    col = [c.value for c in ws[1]].index("السكن") + 1
    ws.cell(row=2, column=col).value = "بعيد"  # changed in Excel
    wb.save(path)
    page = employees.EmployeesPage(AppContext(db, NoCamera(), IdleFaceWorker()))
    monkeypatch.setattr(employees.QFileDialog, "getOpenFileName", lambda *a, **k: (path, ""))
    shown = []
    monkeypatch.setattr(employees, "info", lambda parent, text, title="": shown.append(text))
    page.import_excel()
    assert db.employees.get(eid)["far"] == 1 and "حُدّث 1" in shown[0]
