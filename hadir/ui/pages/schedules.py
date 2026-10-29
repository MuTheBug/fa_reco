"""Work schedules (weekly or rotating), with change-from-a-date support."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)

from ...core.timeutil import WEEK_ORDER, WEEKDAYS_AR, digits, fmt_time, parse_date, parse_time
from ...db.schema import SEED_END, SEED_RULES, SEED_START
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


def plus_minutes(t: time, minutes: int) -> time:
    return (datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)).time()


def day_limits(start: time, grace_late: int, absent_after: int) -> tuple[time, time | None]:
    """Until when an arrival is on time, and after when it is an absence (None = never)."""
    return plus_minutes(start, grace_late), plus_minutes(start, absent_after) if absent_after else None


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
        self.start = TimeEdit(parse_time(SEED_START))
        self.end = TimeEdit(parse_time(SEED_END))
        self.hint = QLabel("")
        self.next_day = QLabel("")
        self.next_day.setStyleSheet("color: #b45309;")
        self.limits = (10, 0)
        self.work.toggled.connect(self._toggle)
        self.start.timeChanged.connect(self._refresh)
        self.end.timeChanged.connect(self._refresh)

    def _toggle(self, on):
        self.start.setEnabled(on)
        self.end.setEnabled(on)
        self._refresh()

    def set_limits(self, grace_late: int, absent_after: int):
        self.limits = (grace_late, absent_after)
        self._refresh()

    def _refresh(self, *_):
        on = self.work.isChecked()
        self.next_day.setText("ينتهي في اليوم التالي" if on and self.end.value() <= self.start.value() else "")
        if not on:
            self.hint.setText("")
            return
        on_time, absent = day_limits(self.start.value(), *self.limits)
        text = f"لا تأخير حتى {fmt_time(on_time)}"
        if absent:
            text += f" • غياب بعد {fmt_time(absent)}"
        self.hint.setText(text)
        self.hint.setStyleSheet("color: #64748b;")

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

        self.days_card = Card("أيام الدوام وأوقاته (يمكن تحديد أوقات مختلفة لأي يوم)")
        self.days_grid = QGridLayout()
        self.days_card.lay.addLayout(self.days_grid)
        fill = button("نسخ وقت أول يوم دوام إلى بقية أيام الدوام", kind="ghost")
        fill.clicked.connect(self._fill)
        self.days_card.lay.addWidget(fill, 0, Qt.AlignRight)
        lay.addWidget(self.days_card)
        self.rows: list[DayRow] = []

        rules = Card("قواعد الاحتساب")
        rf = QFormLayout()
        self.grace_late = SpinBox(0, 240, SEED_RULES["grace_late"], " دقيقة من بداية الدوام")
        self.late_after = QCheckBox("يُحسب التأخير بعد انتهاء فترة السماح فقط (لا من بداية الدوام)")
        self.absent_after = SpinBox(0, 720, SEED_RULES["absent_after"], " دقيقة من بداية الدوام")
        self.absent_after.setSpecialValueText("لا يُحتسب غياباً")
        self.early_mode = QComboBox()
        self.early_mode.addItem("يُحتسب غياباً (ما لم تكن له إجازة زمنية تغطيه)", True)
        self.early_mode.addItem("يُحتسب خروجاً مبكراً", False)
        self.grace_early = SpinBox(0, 240, 0, " دقيقة")
        self.missing_mode = QComboBox()
        self.missing_mode.addItem("يُحتسب غياباً", True)
        self.missing_mode.addItem("يُعلَّم «بصمة ناقصة» فقط", False)
        self.has_exit_until = QCheckBox("لا تُقبل بصمة الخروج بعد الساعة")
        self.exit_until = TimeEdit(parse_time(SEED_RULES["exit_until"]))
        self.has_exit_until.toggled.connect(self.exit_until.setEnabled)
        self.win_before = SpinBox(0, 12, 4, " ساعة")
        self.win_after = SpinBox(0, 16, 8, " ساعة")
        self.overtime = SpinBox(0, 600, 30, " دقيقة")
        rf.addRow("لا يُعدّ متأخراً خلال:", self.grace_late)
        rf.addRow("", self.late_after)
        rf.addRow("يُعدّ غائباً إذا حضر بعد:", self.absent_after)
        rf.addRow("الخروج قبل وقت الخروج:", self.early_mode)
        rf.addRow("سماحية الخروج قبل الوقت:", self.grace_early)
        rf.addRow("عدم تسجيل الحضور أو الانصراف:", self.missing_mode)
        last = QHBoxLayout()
        last.addWidget(self.has_exit_until)
        last.addWidget(self.exit_until)
        last.addStretch(1)
        rf.addRow("آخر وقت لبصمة الخروج:", last)
        rf.addRow("تُقبل بصمة الحضور قبل بداية الدوام بـ:", self.win_before)
        rf.addRow("تُقبل بصمة الانصراف بعد نهايته بـ:", self.win_after)
        rf.addRow("أقل مدة تُحسب عملاً إضافياً:", self.overtime)
        rules.lay.addLayout(rf)
        self.grace_late.valueChanged.connect(self._limits_changed)
        self.absent_after.valueChanged.connect(self._limits_changed)
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
            for c, w in enumerate((row.label, row.work, label("الدوام من"), row.start, label("الخروج"),
                                   row.end, row.hint, row.next_day)):
                self.days_grid.addWidget(w, r, c)
            self.rows.append(row)
        for i, row in enumerate(self.rows):
            if weekly:
                row.set(row.index not in (4, 5), parse_time(SEED_START), parse_time(SEED_END))  # Fri/Sat off
            elif i < len(old) and old[i][0]:
                row.set(*old[i])
            else:
                row.set(i == 0, time(8, 0), time(8, 0))  # a 24-hour duty then rest days
        self._limits_changed()

    def _limits_changed(self, *_):
        for r in self.rows:
            r.set_limits(self.grace_late.value(), self.absent_after.value())

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
            self.has_exit_until.setChecked(True)
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
        self.absent_after.setValue(row["absent_after"])
        self.early_mode.setCurrentIndex(self.early_mode.findData(bool(row["early_absent"])))
        self.missing_mode.setCurrentIndex(self.missing_mode.findData(bool(row["missing_absent"])))
        last = parse_time(row["exit_until"])
        self.has_exit_until.setChecked(last is not None)
        self.exit_until.setEnabled(last is not None)
        if last:
            self.exit_until.set_value(last)
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
                    absent_after=self.absent_after.value(), early_absent=self.early_mode.currentData(),
                    missing_absent=self.missing_mode.currentData(),
                    exit_until=self.exit_until.value() if self.has_exit_until.isChecked() else None,
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



class RulesOverview(Card):
    """Working times and attendance rules of the schedules in use, day by day (Settings page)."""

    def __init__(self, db, parent=None):
        super().__init__("أوقات الدوام وقواعد التأخير والغياب", parent)
        self.db = db
        top = QHBoxLayout()
        self.pick = QComboBox()
        edit = button("تعديل الأوقات والقواعد…", "edit", "primary")
        edit.clicked.connect(self._edit)
        top.addWidget(label("الجدول:"))
        top.addWidget(self.pick, 1)
        top.addWidget(edit)
        self.lay.addLayout(top)
        self.table = DataTable([("day", "اليوم", "text"), ("start", "بداية الدوام", "text"),
                                ("on_time", "لا تأخير حتى", "text"), ("absent", "غياب بعد", "text"),
                                ("exit", "وقت الخروج", "text"), ("last", "آخر بصمة خروج", "text")],
                               stretch="day")
        self.table.setMinimumHeight(300)
        self.lay.addWidget(self.table)
        self.rules = label("", wrap=True)
        self.lay.addWidget(self.rules)
        self.lay.addWidget(label("لأوقات مختلفة ليوم معيّن (مثل الخميس) غيّر وقت ذلك اليوم في نافذة التعديل."
                                 " ولمجموعة موظفين بدوام مختلف أنشئ جدولاً من صفحة «جداول الدوام».",
                                 "Hint", wrap=True))
        self.pick.currentIndexChanged.connect(self._show)

    def reload(self):
        """List the schedules in use from today on, the default group's first."""
        db = self.db
        today = db.now().date().isoformat()
        rows = db.q("SELECT DISTINCT a.schedule_id, s.name, a.group_id, g.name AS group_name FROM"
                    " schedule_assignments a JOIN schedules s ON s.id = a.schedule_id LEFT JOIN emp_groups g"
                    " ON g.id = a.group_id WHERE a.end_date IS NULL OR a.end_date >= ?", (today,))
        default = db.employees.default_group()
        first = db.groups.current_schedule(default) if default else None
        used: dict[int, list] = {}
        for r in rows:
            used.setdefault(r["schedule_id"], [r["name"], []])
            if r["group_name"]:
                used[r["schedule_id"]][1].append(r["group_name"])
        if not used:
            used = {s["id"]: [s["name"], []] for s in db.schedules.list(include_inactive=False)}
        order = sorted(used, key=lambda sid: (first is None or sid != first["id"], used[sid][0]))
        keep = self.pick.currentData()
        self.pick.blockSignals(True)
        self.pick.clear()
        for sid in order:
            name, groups = used[sid]
            self.pick.addItem(name + (f" — {'، '.join(sorted(set(groups)))}" if groups else ""), sid)
        self.pick.setCurrentIndex(max(0, self.pick.findData(keep)))
        self.pick.blockSignals(False)
        self._show()

    def _show(self, *_):
        sid = self.pick.currentData()
        s = self.db.schedules.get(sid) if sid else None
        if s is None:
            self.table.set_rows([])
            self.rules.setText("لا يوجد جدول دوام.")
            return
        if s.kind == "weekly":
            titles = [(i, WEEKDAYS_AR[i]) for i in WEEK_ORDER]
        else:
            titles = [(i, f"اليوم {digits(i + 1)}") for i in range(len(s.days))]
        last = fmt_time(s.exit_until) if s.exit_until else "—"
        rows = []
        for i, title in titles:
            r = s.days[i] if i < len(s.days) else None
            if r is None or not r.workday:
                rows.append({"day": title, "start": "عطلة", "_tone": "muted"})
                continue
            on_time, absent = day_limits(r.start, s.grace_late, s.absent_after)
            rows.append({"day": title, "start": fmt_time(r.start), "on_time": fmt_time(on_time),
                         "absent": fmt_time(absent) if absent else "—", "exit": fmt_time(r.end), "last": last})
        self.table.set_rows(rows)
        early = "غياب (إلا بإجازة زمنية)" if s.early_absent else "خروج مبكر"
        missing = "غياب" if s.missing_absent else "بصمة ناقصة"
        late = "لا يُحتسب غياباً" if not s.absent_after else f"بعد {digits(s.absent_after)} دقيقة غياب"
        self.rules.setText(f"التأخير: بعد {digits(s.grace_late)} دقيقة من بداية الدوام، و{late} •"
                           f" الخروج قبل وقته: {early} • عدم تسجيل الحضور أو الانصراف: {missing}")

    def _edit(self):
        sid = self.pick.currentData()
        if not sid:
            return
        if ScheduleDialog(self.db, sid, False, self.window()).exec() == QDialog.Accepted:
            self.reload()
