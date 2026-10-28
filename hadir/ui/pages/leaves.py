"""Leaves, leave balances and leave types."""

from __future__ import annotations

from datetime import time

from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core.timeutil import digits
from ...db.store import StoreError
from ...reports import builtin
from ...reports import sources as S
from ..admin import Page
from ..widgets.common import (DataTable, DateEdit, EmployeeCombo, PageHeader, PeriodPicker,
                              ReasonDialog, SpinBox, TimeEdit, button, digit_locale, error, label)


class LeavesPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        header = PageHeader("الإجازات", "الإجازات الاعتيادية والمرضية والزمنية والواجبات الرسمية وأرصدتها")
        self.lay.addWidget(header)
        add = button("إضافة إجازة", "plus", "primary")
        add.clicked.connect(self.add_leave)
        header.add(add)
        self.tabs = QTabWidget()
        self.lay.addWidget(self.tabs, 1)

        # --- list
        w = QWidget()
        lay = QVBoxLayout(w)
        f = QHBoxLayout()
        self.period = PeriodPicker("this_month")
        self.emp = EmployeeCombo(self.db, allow_all=True)
        self.type = QComboBox()
        self.cancelled = QCheckBox("إظهار الملغاة")
        f.addWidget(self.period)
        f.addWidget(self.emp)
        f.addWidget(self.type)
        f.addWidget(self.cancelled)
        f.addStretch(1)
        lay.addLayout(f)
        self.table = DataTable([(k, t, kind) for k, t, kind, _ in S.LEAVE_FIELDS if k != "group"],
                               stretch="name")
        self.table.setMinimumHeight(360)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        cancel = button("إلغاء الإجازة المحددة", "x", "danger")
        cancel.clicked.connect(self.cancel_leave)
        row.addWidget(cancel)
        row.addStretch(1)
        self.count = label("", "Hint")
        row.addWidget(self.count)
        lay.addLayout(row)
        self.tabs.addTab(w, "سجل الإجازات")
        for sig in (self.period.changed, self.emp.currentIndexChanged, self.type.currentIndexChanged,
                    self.cancelled.toggled):
            sig.connect(self.reload)

        # --- balances
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        f2 = QHBoxLayout()
        self.year = SpinBox(2000, 2100, self.db.now().year)
        self.year.setGroupSeparatorShown(False)
        self.year.valueChanged.connect(self.reload_balances)
        f2.addWidget(label("السنة:"))
        f2.addWidget(self.year)
        f2.addStretch(1)
        l2.addLayout(f2)
        self.bal = DataTable([("code", "الرقم", "text"), ("name", "الاسم", "text")], stretch="name")
        l2.addWidget(self.bal, 1)
        self.tabs.addTab(w2, "أرصدة الإجازات")

        # --- types
        w3 = QWidget()
        l3 = QVBoxLayout(w3)
        self.types = DataTable([("name", "النوع", "text"), ("short", "الرمز", "text"),
                                ("unit_ar", "الوحدة", "text"), ("paid_ar", "مدفوعة", "text"),
                                ("present_ar", "تُحسب حضوراً", "text"), ("quota_ar", "الرصيد", "text"),
                                ("state", "الحالة", "text")], stretch="name")
        self.types.activated_row.connect(lambda r: self.edit_type(r["id"]))
        l3.addWidget(self.types, 1)
        r3 = QHBoxLayout()
        nt = button("نوع جديد", "plus")
        nt.clicked.connect(lambda: self.edit_type(None))
        et = button("تعديل", "edit")
        et.clicked.connect(lambda: self.types.selected() and self.edit_type(self.types.selected()["id"]))
        r3.addWidget(nt)
        r3.addWidget(et)
        r3.addStretch(1)
        l3.addLayout(r3)
        self.tabs.addTab(w3, "أنواع الإجازات")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())

    def refresh(self):
        self.emp.reload()
        cur = self.type.currentData()
        self.type.blockSignals(True)
        self.type.clear()
        self.type.addItem("كل الأنواع", None)
        for t in self.db.leaves.types():
            self.type.addItem(t["name"], t["id"])
        self.type.setCurrentIndex(max(0, self.type.findData(cur)))
        self.type.blockSignals(False)
        i = self.tabs.currentIndex()
        if i == 0:
            self.reload()
        elif i == 1:
            self.reload_balances()
        else:
            self.reload_types()

    def reload(self):
        start, end = self.period.range()
        emp = self.emp.current_id()
        ids = [emp] if emp else S.select_employees(self.db, start, end, include_inactive=True)
        rows = S.leave_rows(self.db, start, end, ids, self.type.currentData(), self.cancelled.isChecked())
        self.table.set_rows(rows)
        self.count.setText(f"العدد: {digits(len(rows))}")

    def reload_balances(self):
        rep = builtin.build(self.db, "leave_balance", {"year": self.year.value()})
        cols = [(c.key, c.title.replace("\n", " "), c.kind) for c in rep.columns]
        self.bal.model_.columns = cols
        self.bal.set_rows(rep.rows)

    def reload_types(self):
        rows = []
        for t in self.db.leaves.types():
            unit = "ساعة" if t["unit"] == "hour" else "يوم"
            quota = "" if t["quota"] is None else (
                f"{digits(t['quota']):s} {unit}/{'شهر' if t['quota_period'] == 'month' else 'سنة'}")
            rows.append(dict(t) | {"unit_ar": "ساعات (زمنية)" if t["unit"] == "hour" else "أيام",
                                   "paid_ar": "نعم" if t["paid"] else "لا",
                                   "present_ar": "نعم" if t["counts_present"] else "",
                                   "quota_ar": quota or "غير محدد",
                                   "state": "فعّال" if t["active"] else "معطّل"})
        self.types.set_rows(rows)

    def add_leave(self):
        dlg = LeaveDialog(self.ctx, self.emp.current_id(), self)
        if dlg.exec() == QDialog.Accepted:
            self.toast("تمت إضافة الإجازة", "ok")
            self.refresh()

    def cancel_leave(self):
        r = self.table.selected()
        if r is None:
            error(self, "اختر إجازة أولاً.")
            return
        if r["state"] == "ملغاة":
            error(self, "الإجازة ملغاة مسبقاً.")
            return
        reason = ReasonDialog.ask(self, "إلغاء إجازة", f"{r['name']} — {r['type']}")
        if reason:
            self.db.leaves.cancel(r["leave_id"], reason)
            self.reload()

    def edit_type(self, type_id):
        dlg = LeaveTypeDialog(self.db, type_id, self)
        if dlg.exec() == QDialog.Accepted:
            self.reload_types()


class LeaveDialog(QDialog):
    def __init__(self, ctx, employee_id=None, parent=None):
        super().__init__(parent)
        self.db = ctx.db
        self.setWindowTitle("إضافة إجازة")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.emp = EmployeeCombo(self.db, include_inactive=False)
        if employee_id:
            self.emp.set_id(employee_id)
        self.type = QComboBox()
        for t in self.db.leaves.types(include_inactive=False):
            self.type.addItem(t["name"], dict(t))
        today = self.db.now().date()
        self.start = DateEdit(today)
        self.end = DateEdit(today)
        self.t_from = TimeEdit(time(8, 0))
        self.t_to = TimeEdit(time(10, 0))
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("رقم الأمر الإداري وتاريخه (اختياري)")
        self.note = QLineEdit()
        form.addRow("الموظف:", self.emp)
        form.addRow("نوع الإجازة:", self.type)
        self.date_row = QHBoxLayout()
        self.date_row.addWidget(self.start)
        self.to_lbl = label("إلى")
        self.date_row.addWidget(self.to_lbl)
        self.date_row.addWidget(self.end)
        self.date_row.addStretch(1)
        form.addRow("التاريخ:", self.date_row)
        self.time_w = QWidget()
        tr = QHBoxLayout(self.time_w)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.addWidget(label("من الساعة"))
        tr.addWidget(self.t_from)
        tr.addWidget(label("إلى"))
        tr.addWidget(self.t_to)
        tr.addStretch(1)
        form.addRow("الوقت:", self.time_w)
        form.addRow("المرجع:", self.ref)
        form.addRow("ملاحظات:", self.note)
        lay.addLayout(form)
        self.balance = label("", "Hint", wrap=True)
        lay.addWidget(self.balance)
        row = QHBoxLayout()
        ok = QPushButton("حفظ")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self.save)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        row.addStretch(1)
        lay.addLayout(row)
        self.type.currentIndexChanged.connect(self._type_changed)
        self.emp.currentIndexChanged.connect(self._show_balance)
        self.start.dateChanged.connect(self._show_balance)
        self._type_changed()

    def _type_changed(self):
        t = self.type.currentData() or {}
        hourly = t.get("unit") == "hour"
        self.time_w.setVisible(hourly)
        self.end.setVisible(not hourly)
        self.to_lbl.setVisible(not hourly)
        self._show_balance()

    def _show_balance(self):
        t = self.type.currentData() or {}
        emp = self.emp.current_id()
        if not emp or not t:
            self.balance.clear()
            return
        start = self.start.value()
        monthly = t.get("quota_period") == "month"
        bal = self.db.leaves.balance(emp, start.year, start.month if monthly else None)
        b = next((x for x in bal if x["type_id"] == t["id"]), None)
        if b is None:
            return
        unit = "ساعة" if t["unit"] == "hour" else "يوم"
        period = "هذا الشهر" if monthly else "هذه السنة"
        used = f"{b['used']:.1f}".rstrip("0").rstrip(".")
        text = f"المستخدم {period}: {digits(used)} {unit}"
        if b["quota"] is not None:
            rem = f"{b['remaining']:.1f}".rstrip("0").rstrip(".")
            text += f" — المتبقي: {digits(rem)} {unit} من {digits(int(b['quota']))}"
        self.balance.setText(text)

    def save(self):
        t = self.type.currentData()
        emp = self.emp.current_id()
        if not emp or not t:
            error(self, "اختر الموظف ونوع الإجازة.")
            return
        try:
            if t["unit"] == "hour":
                self.db.leaves.add(emp, t["id"], self.start.value(), None, self.t_from.value(),
                                   self.t_to.value(), self.ref.text(), self.note.text())
            else:
                self.db.leaves.add(emp, t["id"], self.start.value(), self.end.value(),
                                   reference=self.ref.text(), note=self.note.text())
        except StoreError as exc:
            error(self, str(exc))
            return
        self.accept()


class LeaveTypeDialog(QDialog):
    def __init__(self, db, type_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.type_id = type_id
        self.setWindowTitle("نوع إجازة")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.short = QLineEdit()
        self.short.setMaxLength(2)
        self.unit = QComboBox()
        self.unit.addItem("أيام", "day")
        self.unit.addItem("ساعات (إجازة زمنية)", "hour")
        self.paid = QCheckBox("مدفوعة الأجر")
        self.present = QCheckBox("تُحسب حضوراً (مثل الإيفاد والواجب الرسمي)")
        self.calendar = QCheckBox("تُحسب بالأيام التقويمية (تشمل العطل)، مثل إجازة الأمومة")
        self.has_quota = QCheckBox("له رصيد محدد")
        self.quota = QDoubleSpinBox()
        self.quota.setLocale(digit_locale())
        self.quota.setRange(0, 1000)
        self.period = QComboBox()
        self.period.addItem("في السنة", "year")
        self.period.addItem("في الشهر", "month")
        self.color = QPushButton("#3b82f6")
        self.color.clicked.connect(self._pick)
        self.active = QCheckBox("فعّال")
        form.addRow("الاسم:", self.name)
        form.addRow("الرمز (في السجل الشهري):", self.short)
        form.addRow("الوحدة:", self.unit)
        form.addRow("", self.paid)
        form.addRow("", self.present)
        form.addRow("", self.calendar)
        q = QHBoxLayout()
        q.addWidget(self.has_quota)
        q.addWidget(self.quota)
        q.addWidget(self.period)
        form.addRow("الرصيد:", q)
        form.addRow("اللون:", self.color)
        form.addRow("", self.active)
        lay.addLayout(form)
        row = QHBoxLayout()
        ok = QPushButton("حفظ")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self.save)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        self.has_quota.toggled.connect(self.quota.setEnabled)
        self.has_quota.toggled.connect(self.period.setEnabled)
        t = db.one("SELECT * FROM leave_types WHERE id = ?", (type_id,)) if type_id else None
        self.paid.setChecked(True)
        self.active.setChecked(True)
        self.has_quota.setChecked(False)
        self.quota.setEnabled(False)
        self.period.setEnabled(False)
        if t:
            self.name.setText(t["name"])
            self.short.setText(t["short"])
            self.unit.setCurrentIndex(self.unit.findData(t["unit"]))
            self.paid.setChecked(bool(t["paid"]))
            self.present.setChecked(bool(t["counts_present"]))
            self.calendar.setChecked(bool(t["calendar_days"]))
            self.has_quota.setChecked(t["quota"] is not None)
            if t["quota"] is not None:
                self.quota.setValue(t["quota"])
            self.period.setCurrentIndex(self.period.findData(t["quota_period"]))
            self.color.setText(t["color"])
            self.active.setChecked(bool(t["active"]))
        self._style_color()

    def _style_color(self):
        self.color.setStyleSheet(f"background: {self.color.text()}; color: white;")

    def _pick(self):
        from PySide6.QtGui import QColor

        c = QColorDialog.getColor(QColor(self.color.text()), self)
        if c.isValid():
            self.color.setText(c.name())
            self._style_color()

    def save(self):
        data = dict(name=self.name.text(), short=self.short.text(), unit=self.unit.currentData(),
                    paid=self.paid.isChecked(), counts_present=self.present.isChecked(),
                    calendar_days=self.calendar.isChecked(),
                    quota=self.quota.value() if self.has_quota.isChecked() else None,
                    quota_period=self.period.currentData(), color=self.color.text(),
                    active=self.active.isChecked())
        try:
            self.db.leaves.save_type(data, self.type_id)
        except StoreError as exc:
            error(self, str(exc))
            return
        self.accept()

