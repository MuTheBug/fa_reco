"""Today at a glance."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QRadioButton, QVBoxLayout)

from ...core import engine as E
from ...core.timeutil import digits, fmt_date_long, fmt_time
from ...db.store import StoreError
from ...kiosk_logic import EXIT_REASONS, exit_state, record_exit
from ...reports import sources as S
from ..admin import Page
from ..theme import C
from ..widgets.common import Card, DataTable, PageHeader, SearchBox, StatTile, TimeEdit, button, error, label


class DashboardPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.header = PageHeader("لوحة اليوم", "")
        self.lay.addWidget(self.header)
        refresh = button("تحديث", "refresh")
        refresh.clicked.connect(self.refresh)
        self.header.add(refresh)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.tiles = {
            "enrolled": StatTile("موظف مسجّل بالوجه", C["navy"], "users"),
            "present": StatTile("حاضر اليوم", C["ok"], "check"),
            "late": StatTile("متأخر", C["warn"], "clock"),
            "absent": StatTile("لم يحضر / غائب", C["bad"], "x"),
            "leave": StatTile("في إجازة", C["info"], "leave"),
            "missing_face": StatTile("بلا بصمة وجه", C["muted"], "face"),
        }
        for i, t in enumerate(self.tiles.values()):
            grid.addWidget(t, 0, i)
        self.lay.addLayout(grid)

        self.alert = QLabel()
        self.alert.setWordWrap(True)
        self.alert.setStyleSheet(f"background: {C['bad_bg']}; color: {C['bad']}; border-radius: 10px;"
                                 " padding: 10px 14px; font-weight: bold;")
        self.alert_btn = button("عرض السجل", "shield", "danger")
        self.alert_btn.clicked.connect(lambda: self.ctx.window.admin.show_page("logs"))
        row = QHBoxLayout()
        row.addWidget(self.alert, 1)
        row.addWidget(self.alert_btn)
        self.lay.addLayout(row)

        card = Card("حالة الموظفين اليوم")
        tools = QHBoxLayout()
        self.group = QComboBox()
        self.group.currentIndexChanged.connect(self.refresh)
        self.search = SearchBox("بحث بالاسم أو الرقم…")
        tools.addWidget(label("المجموعة:"))
        tools.addWidget(self.group)
        tools.addStretch(1)
        self.exit_btn = button("تسجيل خروج", "logout", "primary")
        self.exit_btn.setToolTip("لموظف يغادر أثناء الدوام: إجازة ساعية أو يومية أو دون إجازة")
        self.exit_btn.clicked.connect(self._record_exit)
        tools.addWidget(self.exit_btn)
        tools.addWidget(self.search)
        card.lay.addLayout(tools)
        self.table = DataTable([
            ("code", "الرقم الوظيفي", "text"), ("name", "الاسم", "text"), ("group", "المجموعة", "text"),
            ("shift_start", "بداية الدوام", "time"), ("check_in", "الحضور", "time"),
            ("check_out", "الانصراف", "time"), ("late", "التأخير", "minutes"),
            ("status", "الحالة", "text"), ("notes", "ملاحظات", "text")], stretch="name")
        self.table.setMinimumHeight(380)
        self.table.activated_row.connect(self._open_timeline)
        self.search.textChanged.connect(self.table.filter)
        card.lay.addWidget(self.table)
        self.lay.addWidget(card, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def refresh(self):
        db = self.db
        today = db.now().date()
        self.header.subtitle.setText(fmt_date_long(today))
        current = self.group.currentData()
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("جميع المجموعات", None)
        for g in db.groups.list():
            self.group.addItem(g["name"], g["id"])
        idx = self.group.findData(current)
        self.group.setCurrentIndex(max(0, idx))
        self.group.blockSignals(False)
        groups = [self.group.currentData()] if self.group.currentData() else None

        ids = S.select_employees(db, today, today, groups)
        rows = S.daily_rows(db, today, today, ids, groups, now=db.now())
        rows.sort(key=lambda r: (r["status_code"] != E.ABSENT, r["status_code"] != E.PENDING, r["name"]))
        for r in rows:
            if r["status_code"] == E.PENDING:
                r["_tone"] = "bad" if "overdue" in r["_record"].flags else "muted"
        self.table.set_rows(rows)
        recs = [r["_record"] for r in rows]
        self.tiles["present"].set(sum(1 for r in recs if r.counts_present))
        self.tiles["late"].set(sum(1 for r in recs if r.late))
        self.tiles["absent"].set(sum(1 for r in recs if r.status in (E.ABSENT,) or
                                     (r.status == E.PENDING and "overdue" in r.flags)))
        self.tiles["leave"].set(sum(1 for r in recs if r.status == E.LEAVE and not r.counts_present))
        active = db.scalar("SELECT COUNT(*) FROM employees WHERE active = 1")
        enrolled = db.faces.enrolled_count()
        self.tiles["enrolled"].set(f"{digits(enrolled)} / {digits(active)}")
        self.tiles["missing_face"].set(max(0, active - enrolled))
        unseen = db.events.unseen()
        self.alert.setVisible(bool(unseen))
        self.alert_btn.setVisible(bool(unseen))
        if unseen:
            self.alert.setText(f"يوجد {digits(unseen)} تنبيه أمني جديد (محاولات انتحال، تغيير ساعة الجهاز، دخول فاشل…)")
        self.timer.start(30_000)

    def leave(self):
        self.timer.stop()

    def _open_timeline(self, row):
        self.ctx.window.admin.show_page("timeline", employee_id=row["employee_id"])

    def _record_exit(self):
        row = self.table.selected()
        if row is None:
            error(self, "اختر من الجدول الموظف الذي يغادر.")
            return
        rec, why = exit_state(self.db, row["employee_id"], self.db.now())
        if why:
            error(self, f"{row['name']}: {why}")
            return
        dlg = ExitDialog(self.db, row["employee_id"], row["name"], rec, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.toast(f"سُجّل خروج {row['name']}", "ok")


class ExitDialog(QDialog):
    """An employee leaving during the working day, and why."""

    def __init__(self, db, employee_id: int, name: str, rec: E.DayRecord, parent=None):
        super().__init__(parent)
        self.db, self.employee_id, self.rec = db, employee_id, rec
        self.setWindowTitle(f"تسجيل خروج — {name}")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"{name}: الحضور {fmt_time(rec.check_in)} • نهاية الدوام {fmt_time(rec.shift.end)}",
                            "CardTitle", wrap=True))
        form = QFormLayout()
        now = db.now()
        self.time = TimeEdit(now.time())
        form.addRow("وقت الخروج:", self.time)
        types = [t for t in db.leaves.types(include_inactive=False)]
        self.hourly_types = [t for t in types if t["unit"] == "hour"]
        self.daily_type = QComboBox()
        for t in types:
            if t["unit"] != "hour":
                self.daily_type.addItem(t["name"], t["id"])
        self.choice = QButtonGroup(self)
        self.options = {}
        box = QVBoxLayout()
        for key, text in EXIT_REASONS.items():
            rb = QRadioButton(text)
            self.choice.addButton(rb)
            self.options[key] = rb
            if key == "daily":
                row = QHBoxLayout()
                row.addWidget(rb)
                row.addWidget(self.daily_type, 1)
                box.addLayout(row)
            else:
                box.addWidget(rb)
        self.options["plain"].setText("دون إجازة (يُحسب حسب قواعد الدوام)")
        if not self.hourly_types:
            self.options["hourly"].setEnabled(False)
        (self.options["hourly"] if self.hourly_types else self.options["plain"]).setChecked(True)
        form.addRow("السبب:", box)
        self.note = QLineEdit()
        self.note.setPlaceholderText("اختياري: مثل رقم الإجازة أو سبب المغادرة")
        form.addRow("ملاحظة:", self.note)
        lay.addLayout(form)
        lay.addWidget(label("لا يُسجَّل للموظف خروج ثانٍ في اليوم نفسه.", "Hint", wrap=True))
        buttons = QHBoxLayout()
        ok = QPushButton("تسجيل الخروج")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self.save)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        lay.addLayout(buttons)

    def reason(self) -> str:
        return next(k for k, rb in self.options.items() if rb.isChecked())

    def save(self):
        ts = datetime.combine(self.db.now().date(), self.time.value())
        reason = self.reason()
        type_id = (self.hourly_types[0]["id"] if reason == "hourly" else
                   self.daily_type.currentData() if reason == "daily" else None)
        if ts > self.db.now():
            error(self, "وقت الخروج لم يأتِ بعد.")
            return
        try:
            record_exit(self.db, self.employee_id, ts, reason, type_id, self.note.text())
        except StoreError as exc:
            error(self, str(exc))
            return
        self.accept()
