"""Official holidays."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from ...core.timeutil import digits, parse_date
from ...db.store import StoreError
from ..admin import Page
from ..widgets.common import (Card, CheckList, DataTable, DateEdit, PageHeader, SpinBox, button,
                              confirm, error, label)

# Fixed-date public holidays in Iraq (Official Holidays Law, 2024).  Shown as suggestions
# for the administrator to confirm; lunar holidays are added per year when announced.
FIXED_HOLIDAYS = [
    ((1, 1), "رأس السنة الميلادية"),
    ((1, 6), "عيد الجيش العراقي"),
    ((3, 21), "عيد نوروز"),
    ((5, 1), "عيد العمال العالمي"),
    ((7, 14), "ذكرى ثورة ١٤ تموز"),
    ((10, 3), "اليوم الوطني"),
    ((12, 10), "يوم النصر"),
    ((12, 25), "عيد الميلاد المجيد"),
]


class HolidaysPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        header = PageHeader("العطل الرسمية", "العطل التي لا يُحسب فيها غياب؛ يمكن تخصيص عطلة لمجموعات معينة")
        self.lay.addWidget(header)
        add = button("إضافة عطلة", "plus", "primary")
        add.clicked.connect(lambda: self.edit(None))
        header.add(add)
        fixed = button("العطل الثابتة للسنة…", "calendar")
        fixed.clicked.connect(self.add_fixed)
        header.add(fixed)
        card = Card()
        row = QHBoxLayout()
        self.year = SpinBox(2000, 2100, self.db.now().year)
        self.year.setGroupSeparatorShown(False)
        self.year.valueChanged.connect(self.refresh)
        row.addWidget(label("السنة:"))
        row.addWidget(self.year)
        row.addStretch(1)
        card.lay.addLayout(row)
        self.table = DataTable([("name", "العطلة", "text"), ("start", "من", "date"), ("end", "إلى", "date"),
                                ("days", "الأيام", "int"), ("groups", "تشمل", "text"),
                                ("note", "ملاحظات", "text")], stretch="name")
        self.table.setMinimumHeight(360)
        self.table.activated_row.connect(lambda r: self.edit(r["id"]))
        card.lay.addWidget(self.table)
        r2 = QHBoxLayout()
        e = button("تعديل", "edit")
        e.clicked.connect(lambda: self.table.selected() and self.edit(self.table.selected()["id"]))
        d = button("حذف", "trash", "danger")
        d.clicked.connect(self.delete)
        r2.addWidget(e)
        r2.addWidget(d)
        r2.addStretch(1)
        card.lay.addLayout(r2)
        self.lay.addWidget(card, 1)

    def refresh(self):
        names = self.db.groups.names()
        rows = []
        for h in self.db.holidays.list(self.year.value()):
            s, e = parse_date(h["start_date"]), parse_date(h["end_date"])
            rows.append(h | {"start": s, "end": e, "days": (e - s).days + 1,
                             "groups": "جميع الموظفين" if not h["group_ids"] else
                             "، ".join(names.get(g, "") for g in h["group_ids"])})
        self.table.set_rows(rows)

    def edit(self, holiday_id):
        dlg = HolidayDialog(self.db, holiday_id, self.year.value(), self)
        if dlg.exec() == QDialog.Accepted:
            self.toast("تم الحفظ", "ok")
        self.refresh()

    def delete(self):
        r = self.table.selected()
        if r and confirm(self, f"حذف العطلة «{r['name']}»؟ ستُحسب أيامها أيام دوام عادية."):
            self.db.holidays.delete(r["id"])
            self.refresh()

    def add_fixed(self):
        year = self.year.value()
        existing = {(h["start_date"], h["name"]) for h in self.db.holidays.list(year)}
        dlg = QDialog(self)
        dlg.setWindowTitle(f"العطل الثابتة لسنة {digits(year)}")
        lay = QVBoxLayout(dlg)
        lay.addWidget(label("اختر العطل التي تريد إضافتها، وتحقّق منها مع التعليمات الرسمية النافذة."
                            " العطل الدينية (الأعياد ورأس السنة الهجرية وغيرها) تُضاف بتواريخها كل سنة.",
                            wrap=True))
        lst = QListWidget()
        for (m, d), name in FIXED_HOLIDAYS:
            day = date(year, m, d)
            it = QListWidgetItem(f"{digits(day.strftime('%Y/%m/%d'))} — {name}")
            it.setData(Qt.UserRole, (day, name))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            already = (day.isoformat(), name) in existing
            it.setCheckState(Qt.Unchecked if already else Qt.Checked)
            if already:
                it.setText(it.text() + " (مضافة)")
            lst.addItem(it)
        lay.addWidget(lst)
        row = QHBoxLayout()
        ok = QPushButton("إضافة المحدد")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(dlg.accept)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(dlg.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        if dlg.exec() != QDialog.Accepted:
            return
        n = 0
        with self.db.tx():
            for i in range(lst.count()):
                it = lst.item(i)
                if it.checkState() == Qt.Checked:
                    day, name = it.data(Qt.UserRole)
                    if (day.isoformat(), name) not in existing:
                        self.db.holidays.save(name, day, day)
                        n += 1
        self.toast(f"أُضيفت {digits(n)} عطلة", "ok")
        self.refresh()


class HolidayDialog(QDialog):
    def __init__(self, db, holiday_id, year: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.holiday_id = holiday_id
        self.setWindowTitle("عطلة رسمية")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        today = db.now().date()
        start = today if today.year == year else date(year, 1, 1)
        self.start = DateEdit(start)
        self.end = DateEdit(start)
        self.note = QLineEdit()
        self.all = QCheckBox("تشمل جميع الموظفين")
        self.groups = CheckList([(g["id"], g["name"]) for g in db.groups.list(include_inactive=True)])
        self.groups.setMaximumHeight(140)
        form.addRow("اسم العطلة:", self.name)
        r = QHBoxLayout()
        r.addWidget(self.start)
        r.addWidget(label("إلى"))
        r.addWidget(self.end)
        r.addStretch(1)
        form.addRow("من:", r)
        form.addRow("", self.all)
        form.addRow("المجموعات:", self.groups)
        form.addRow("ملاحظات:", self.note)
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
        self.all.toggled.connect(lambda on: self.groups.setEnabled(not on))
        self.all.setChecked(True)
        self.start.dateChanged.connect(lambda *_: self.end.value() < self.start.value()
                                       and self.end.set_value(self.start.value()))
        h = next((x for x in db.holidays.list() if x["id"] == holiday_id), None) if holiday_id else None
        if h:
            self.name.setText(h["name"])
            self.start.set_value(parse_date(h["start_date"]))
            self.end.set_value(parse_date(h["end_date"]))
            self.note.setText(h["note"])
            self.all.setChecked(not h["group_ids"])
            self.groups.set_checked(h["group_ids"])

    def save(self):
        groups = [] if self.all.isChecked() else self.groups.checked()
        if not self.all.isChecked() and not groups:
            error(self, "اختر مجموعة واحدة على الأقل أو اجعلها لجميع الموظفين.")
            return
        try:
            self.db.holidays.save(self.name.text(), self.start.value(), self.end.value(), groups,
                                  self.note.text(), self.holiday_id)
        except StoreError as exc:
            error(self, str(exc))
            return
        self.accept()
