"""Build every screen and dialog with demo data (no camera, no models needed)."""

from datetime import date

import pytest
from PySide6.QtCore import QObject, Signal


class StubWorker(QObject):
    ready = Signal(str)
    progress = Signal(int, int)
    status = Signal(object)
    decision = Signal(object, object)
    enroll_status = Signal(object)
    analysis = Signal(object, object)

    engine = None
    ready_ok = False
    load_error = ""
    mode = "idle"

    def set_mode(self, mode):
        self.mode = mode

    def configure(self, settings):
        pass

    def reload_galleries(self):
        pass

    def pause(self, timeout=0):
        return self.mode

    def stop(self):
        pass


class StubCamera:
    state, error, fps, brightness = "error", "لا توجد كاميرا", 0.0, 0.0

    def latest(self):
        return None, 0

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("HADIR_DATA_DIR", str(tmp_path))
    from hadir.demo import demo_database
    from hadir.ui.main_window import AppContext, MainWindow

    db = demo_database(tmp_path / "ui.db", date(2026, 10, 28), employees=8, days=20)
    ctx = AppContext(db, StubCamera(), StubWorker())
    win = MainWindow(ctx, kiosk_first=False)
    win.resize(1366, 768)
    yield win
    win._allow_close = True
    win.close()


def test_every_page_builds_and_refreshes(window, qapp):
    from hadir.ui.admin import PAGES

    window.start()
    for key, *_ in PAGES:
        window.admin.show_page(key)
        qapp.processEvents()
        page = window.admin.pages[key]
        page.refresh()
    window.admin.show_page("timeline", employee_id=window.ctx.db.scalar("SELECT id FROM employees"))
    tl = window.admin.pages["timeline"]
    assert tl.days.model_.rowCount() > 0
    tl.days.selectRow(3)
    tl._select_day()
    dash = window.admin.pages["dashboard"]
    assert dash.table.model_.rowCount() == 8


def test_dialogs_build(window, qapp):
    from hadir.ui.pages.employees import EmployeeDialog
    from hadir.ui.pages.holidays import HolidayDialog
    from hadir.ui.pages.leaves import LeaveDialog, LeaveTypeDialog
    from hadir.ui.pages.schedules import ScheduleDialog

    db = window.ctx.db
    emp = db.scalar("SELECT id FROM employees")
    for dlg in (EmployeeDialog(window.ctx, emp, window), EmployeeDialog(window.ctx, None, window),
                ScheduleDialog(db, db.scalar("SELECT id FROM schedules"), False, window),
                ScheduleDialog(db, None, False, window), LeaveDialog(window.ctx, emp, window),
                LeaveTypeDialog(db, None, window), HolidayDialog(db, None, 2026, window)):
        dlg.show()
        qapp.processEvents()
        dlg.close()


def test_schedule_dialog_round_trip(window):
    from hadir.ui.pages.schedules import ScheduleDialog

    db = window.ctx.db
    sid = db.scalar("SELECT id FROM schedules WHERE name = 'الدوام الرسمي'")
    dlg = ScheduleDialog(db, sid, False, window)
    data = dlg._data()
    sunday = data["days"][6]
    assert sunday[0] and sunday[1].strftime("%H:%M") == "08:00" and sunday[2].strftime("%H:%M") == "14:00"
    assert not data["days"][4][0]  # Friday off


def test_report_builder_runs(window, qapp):
    window.start()
    window.admin.show_page("reports")
    page = window.admin.pages["reports"]
    b = page.builder
    b.source.setCurrentIndex(b.source.findData("summary"))
    b.run("table")
    assert b.preview_table.model_.rowCount() == 8
    d = b.definition()
    b.load(d)
    assert b.definition()["columns"] == d["columns"]


def test_kiosk_shows_result(window, qapp):
    from datetime import datetime

    from hadir.kiosk_logic import KioskLogic

    db = window.ctx.db
    emp = db.scalar("SELECT id FROM employees")
    res = KioskLogic(db).register(emp, datetime(2026, 10, 28, 10, 30))
    window.kiosk.show_result(res)
    assert window.kiosk.result.isVisibleTo(window.kiosk) and res.kind == "punched"
    again = KioskLogic(db).register(emp, datetime(2026, 10, 28, 10, 31))
    assert again.kind == "duplicate"


def test_time_fields_are_not_mirrored(qapp):
    from datetime import time

    from hadir.ui.widgets.common import DateEdit, TimeEdit

    assert TimeEdit(time(8, 0)).text() == "08:00"
    assert DateEdit(date(2026, 10, 31)).text() == "2026/10/31"


def test_tables_keep_given_order_until_header_click(qapp):
    from PySide6.QtCore import Qt

    from hadir.ui.widgets.common import DataTable

    t = DataTable([("name", "الاسم", "text"), ("score", "الدرجة", "float")])
    t.set_rows([{"name": "ب", "score": 0.9}, {"name": "أ", "score": 0.1}])
    first = t.model().index(0, 0).data()
    assert first == "ب"
    t.sortByColumn(1, Qt.AscendingOrder)
    assert t.model().index(0, 0).data() == "أ"
