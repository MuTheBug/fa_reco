"""Work schedules (weekly or rotating), with change-from-a-date support."""

from __future__ import annotations

from datetime import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)

from ...core.timeutil import WEEK_ORDER, WEEKDAYS_AR, digits, fmt_time, parse_date, parse_time
from ...db.store import StoreError
from ..admin import Page
from ..widgets.common import (Card, DataTable, DateEdit, PageHeader, SpinBox, TimeEdit, button,
                              confirm, error, label)


def schedule_summary(db, sid: int) -> str:
    s = db.schedules.get(sid)
    if s is None:
        return ""
    if s.kind == "cycle":
        parts = []
        for r in s.days:
            if r.workday:
                parts.append(f"دوام {fmt_time(r.start)}–{fmt_time(r.end)}")
            else:
                parts.append("راحة")
        return f"دورة {digits(len(s.days))} أيام: " + "، ".join(parts)
    work = [i for i in WEEK_ORDER if s.days[i].workday]
    times = {(s.days[i].start, s.days[i].end) for i in work}
    names = "، ".join(WEEKDAYS_AR[i] for i in work)
    if len(times) == 1:
        st, en = next(iter(times))
        over = " (ينتهي في اليوم التالي)" if en <= st else ""
        return f"{names} • {fmt_time(st)}–{fmt_time(en)}{over}"
    return f"{names} • أوقات مختلفة"


class SchedulesPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        header = PageHeader("جداول الدوام", "أيام الدوام وأوقاته، فترة السماح، وقواعد احتساب التأخير والإضافي")
        self.lay.addWidget(header)
        new = button("جدول جديد", "plus", "primary")
        new.clicked.connect(lambda: self.edit(None))
        header.add(new)
        card = Card()
        self.table = DataTable([("name", "الجدول", "text"), ("kind_ar", "النوع", "text"),
                                ("summary", "الأيام والأوقات", "text"), ("grace_late", "سماح التأخير (د)", "int"),
                                ("usage", "مستخدم في", "int"), ("state", "الحالة", "text")], stretch="summary")
        self.table.setMinimumHeight(360)
        self.table.activated_row.connect(lambda r: self.edit(r["id"]))
        card.lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, ic, fn, kind in (("تعديل", "edit", self._edit, ""), ("نسخ", "copy", self._copy, ""),
                                   ("حذف", "trash", self._delete, "danger")):
            b = button(text, ic, kind)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        card.lay.addLayout(row)
        self.lay.addWidget(card, 1)

    def refresh(self):
        rows = []
        for s in self.db.schedules.list():
            rows.append(dict(s) | {"kind_ar": "أسبوعي" if s["kind"] == "weekly" else "دوري (مناوبات)",
                                   "summary": schedule_summary(self.db, s["id"]),
                                   "usage": self.db.schedules.usage(s["id"]),
                                   "state": "فعّال" if s["active"] else "معطّل",
                                   "_tone": "" if s["active"] else "muted"})
        self.table.set_rows(rows)

    def _sel(self):
        r = self.table.selected()
        if r is None:
            error(self, "اختر جدولاً أولاً.")
        return r

    def _edit(self):
        r = self._sel()
        if r:
            self.edit(r["id"])

    def _copy(self):
        r = self._sel()
        if r:
            self.edit(r["id"], copy=True)

    def _delete(self):
        r = self._sel()
        if r and confirm(self, f"حذف الجدول «{r['name']}»؟"):
            try:
                self.db.schedules.delete(r["id"])
            except StoreError as exc:
                error(self, str(exc))
            self.refresh()

    def edit(self, sid, copy: bool = False):
        dlg = ScheduleDialog(self.db, sid, copy, self)
        if dlg.exec() == QDialog.Accepted:
            self.toast("تم حفظ الجدول", "ok")
        self.refresh()


class DayRow:
    def __init__(self, title: str):
        self.label = QLabel(title)
        self.work = QCheckBox("دوام")
        self.start = TimeEdit(time(8, 0))
        self.end = TimeEdit(time(14, 0))
        self.next_day = QLabel("")
        self.work.toggled.connect(self._toggle)
        self.start.timeChanged.connect(self._overnight)
        self.end.timeChanged.connect(self._overnight)

    def _toggle(self, on):
        self.start.setEnabled(on)
        self.end.setEnabled(on)
        self._overnight()

    def _overnight(self):
        on = self.work.isChecked() and self.end.value() <= self.start.value()
        self.next_day.setText("ينتهي في اليوم التالي" if on else "")
        self.next_day.setStyleSheet("color: #b45309;")

    def set(self, workday: bool, st, en):
        self.work.setChecked(workday)
        if st:
            self.start.set_value(st)
        if en:
            self.end.set_value(en)
        self._toggle(workday)

    def value(self):
        if not self.work.isChecked():
            return (False, None, None)
        return (True, self.start.value(), self.end.value())


class ScheduleDialog(QDialog):
    def __init__(self, db, sid, copy: bool, parent=None):
        super().__init__(parent)
        self.db = db
        self.sid = None if copy else sid
        self.source_id = sid
        self.setWindowTitle("جدول دوام")
        self.setMinimumSize(760, 640)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        form = QFormLayout()
        self.name = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItem("أسبوعي (نفس الأيام كل أسبوع)", "weekly")
        self.kind.addItem("دوري / مناوبات (يتكرر كل عدد من الأيام)", "cycle")
        form.addRow("اسم الجدول:", self.name)
        form.addRow("النوع:", self.kind)
        self.cycle_len = SpinBox(1, 62, 3, " يوم")
        self.anchor = DateEdit()
        self.cycle_box = QWidget()
        cb = QHBoxLayout(self.cycle_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.addWidget(label("طول الدورة:"))
        cb.addWidget(self.cycle_len)
        cb.addWidget(label("اليوم الأول من الدورة يوافق:"))
        cb.addWidget(self.anchor)
        cb.addStretch(1)
        form.addRow("", self.cycle_box)
        lay.addLayout(form)

        self.days_card = Card("أيام الدوام وأوقاته")
        self.days_grid = QGridLayout()
        self.days_card.lay.addLayout(self.days_grid)
        fill = button("نسخ وقت أول يوم دوام إلى بقية أيام الدوام", kind="ghost")
        fill.clicked.connect(self._fill)
        self.days_card.lay.addWidget(fill, 0, Qt.AlignRight)
        lay.addWidget(self.days_card)
        self.rows: list[DayRow] = []

        rules = Card("قواعد الاحتساب")
        rf = QFormLayout()
        self.grace_late = SpinBox(0, 240, 10, " دقيقة")
        self.late_after = QCheckBox("يُحسب التأخير بعد انتهاء فترة السماح فقط (لا من بداية الدوام)")
        self.grace_early = SpinBox(0, 240, 0, " دقيقة")
        self.win_before = SpinBox(0, 12, 4, " ساعة")
        self.win_after = SpinBox(0, 16, 8, " ساعة")
        self.overtime = SpinBox(0, 600, 30, " دقيقة")
        rf.addRow("سماحية التأخير:", self.grace_late)
        rf.addRow("", self.late_after)
        rf.addRow("سماحية الخروج المبكر:", self.grace_early)
        rf.addRow("تُقبل بصمة الحضور قبل بداية الدوام بـ:", self.win_before)
        rf.addRow("تُقبل بصمة الانصراف بعد نهايته بـ:", self.win_after)
        rf.addRow("أقل مدة تُحسب عملاً إضافياً:", self.overtime)
        rules.lay.addLayout(rf)
        lay.addWidget(rules)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(50)
        self.active = QCheckBox("فعّال")
        lay.addWidget(label("ملاحظات:"))
        lay.addWidget(self.notes)
        lay.addWidget(self.active)

        self.effective_box = Card("تطبيق التعديل")
        self.apply_from = QCheckBox("هذا الجدول مستخدم: طبّق التعديل اعتباراً من تاريخ محدد"
                                    " (تبقى الأيام السابقة محسوبة بالأوقات القديمة)")
        self.apply_date = DateEdit(db.now().date())
        ef = QHBoxLayout()
        ef.addWidget(self.apply_from, 1)
        ef.addWidget(self.apply_date)
        self.effective_box.lay.addLayout(ef)
        lay.addWidget(self.effective_box)

        row = QHBoxLayout()
        ok = button("حفظ", "check", "primary")
        ok.clicked.connect(self.save)
        cancel = button("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        row.addStretch(1)
        outer.addLayout(row)

        self.kind.currentIndexChanged.connect(self._rebuild_days)
        self.cycle_len.valueChanged.connect(self._rebuild_days)
        self._load(sid, copy)

    def _rebuild_days(self, *_):
        old = [r.value() for r in self.rows]
        while self.days_grid.count():
            w = self.days_grid.takeAt(0).widget()
            if w:
                w.setParent(None)
        self.rows = []
        weekly = self.kind.currentData() == "weekly"
        self.cycle_box.setVisible(not weekly)
        if weekly:
            titles = [(i, WEEKDAYS_AR[i]) for i in WEEK_ORDER]
        else:
            titles = [(i, f"اليوم {digits(i + 1)}") for i in range(self.cycle_len.value())]
        for r, (idx, title) in enumerate(titles):
            row = DayRow(title)
            row.index = idx
            for c, w in enumerate((row.label, row.work, label("من"), row.start, label("إلى"), row.end,
                                   row.next_day)):
                self.days_grid.addWidget(w, r, c)
            self.rows.append(row)
        for i, row in enumerate(self.rows):
            if weekly:
                row.set(row.index not in (4, 5), time(8, 0), time(14, 0))  # Fri/Sat off
            elif i < len(old) and old[i][0]:
                row.set(*old[i])
            else:
                row.set(i == 0, time(8, 0), time(8, 0))  # a 24-hour duty then rest days

    def _fill(self):
        first = next((r for r in self.rows if r.work.isChecked()), None)
        if first is None:
            return
        for r in self.rows:
            if r.work.isChecked():
                r.start.set_value(first.start.value())
                r.end.set_value(first.end.value())

    def _load(self, sid, copy):
        self.kind.blockSignals(True)
        self.cycle_len.blockSignals(True)
        row = self.db.one("SELECT * FROM schedules WHERE id = ?", (sid,)) if sid else None
        if row is None:
            self.active.setChecked(True)
            self.kind.blockSignals(False)
            self.cycle_len.blockSignals(False)
            self._rebuild_days()
            self.effective_box.hide()
            return
        self.name.setText(row["name"] + (" (نسخة)" if copy else ""))
        self.kind.setCurrentIndex(self.kind.findData(row["kind"]))
        days = self.db.schedules.days(sid)
        if row["kind"] == "cycle":
            self.cycle_len.setValue(len(days))
            self.anchor.set_value(parse_date(row["cycle_anchor"]))
        self.kind.blockSignals(False)
        self.cycle_len.blockSignals(False)
        self._rebuild_days()
        by_index = {d["day_index"]: d for d in days}
        for r in self.rows:
            d = by_index.get(r.index)
            if d:
                r.set(bool(d["workday"]), parse_time(d["start_time"]), parse_time(d["end_time"]))
        self.grace_late.setValue(row["grace_late"])
        self.late_after.setChecked(bool(row["late_after_grace"]))
        self.grace_early.setValue(row["grace_early"])
        self.win_before.setValue(row["window_before"] // 60)
        self.win_after.setValue(row["window_after"] // 60)
        self.overtime.setValue(row["overtime_min"])
        self.notes.setPlainText(row["notes"])
        self.active.setChecked(bool(row["active"]))
        in_use = self.db.schedules.usage(sid) > 0 and not copy
        self.effective_box.setVisible(in_use)

    def _data(self) -> dict:
        weekly = self.kind.currentData() == "weekly"
        if weekly:
            by = {r.index: r.value() for r in self.rows}
            days = [by[i] for i in range(7)]
        else:
            days = [r.value() for r in self.rows]
        return dict(name=self.name.text(), kind=self.kind.currentData(), days=days,
                    cycle_anchor=None if weekly else self.anchor.value(),
                    grace_late=self.grace_late.value(), late_after_grace=self.late_after.isChecked(),
                    grace_early=self.grace_early.value(), window_before=self.win_before.value() * 60,
                    window_after=self.win_after.value() * 60, overtime_min=self.overtime.value(),
                    notes=self.notes.toPlainText(), active=self.active.isChecked())

    def save(self):
        data = self._data()
        try:
            if self.sid and self.effective_box.isVisible() and self.apply_from.isChecked():
                self._save_from_date(data)
            else:
                self.sid = self.db.schedules.save(data, self.sid)
        except StoreError as exc:
            error(self, str(exc))
            return
        self.accept()

    def _save_from_date(self, data: dict):
        """Keep the old schedule for past days; a new copy takes over from the chosen date."""
        start = self.apply_date.value()
        db = self.db
        old_name = db.scalar("SELECT name FROM schedules WHERE id = ?", (self.sid,))
        if data["name"].strip() == old_name:
            data["name"] = f"{old_name} (من {start:%Y/%m/%d})"
        with db.tx():
            new_id = db.schedules.save(data)
            users = db.q("SELECT * FROM schedule_assignments WHERE schedule_id = ? AND start_date <= ?"
                         " AND (end_date IS NULL OR end_date >= ?)", (self.sid, start.isoformat(),
                                                                       start.isoformat()))
            later = db.q("SELECT * FROM schedule_assignments WHERE schedule_id = ? AND start_date > ?",
                         (self.sid, start.isoformat()))
            for a in users:
                db.schedules.assign(new_id, start, a["end_date"], group_id=a["group_id"],
                                    employee_id=a["employee_id"], note=f"تعديل «{old_name}»")
            for a in later:
                db.x("UPDATE schedule_assignments SET schedule_id = ? WHERE id = ?", (new_id, a["id"]))
            db.audit("تعديل جدول اعتباراً من تاريخ", "schedule", self.sid,
                     {"new_schedule": new_id, "from": start.isoformat()})
        self.sid = new_id

