"""Groups and their dated schedule assignments."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QVBoxLayout)

from ...core.timeutil import digits, parse_date
from ...db.store import StoreError
from ..admin import Page
from ..widgets.common import (Card, DataTable, DateEdit, PageHeader, button, confirm, error, label)


class GroupsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        header = PageHeader("المجموعات", "كل مجموعة تتبع جدول دوام؛ يمكن تغيير الجدول اعتباراً من تاريخ"
                                         " دون المساس بالأيام السابقة")
        self.lay.addWidget(header)
        new = button("مجموعة جديدة", "plus", "primary")
        new.clicked.connect(self.new_group)
        header.add(new)

        body = QHBoxLayout()
        left = Card("المجموعات")
        self.table = DataTable([("name", "المجموعة", "text"), ("members", "الموظفون", "int"),
                                ("schedule", "الجدول الحالي", "text"), ("state", "الحالة", "text")],
                               stretch="name")
        self.table.clicked.connect(lambda *_: self._select())
        left.lay.addWidget(self.table)
        body.addWidget(left, 2)

        right = QVBoxLayout()
        self.editor = Card("بيانات المجموعة")
        form = QFormLayout()
        self.name = QLineEdit()
        self.desc = QPlainTextEdit()
        self.desc.setFixedHeight(60)
        self.active = QCheckBox("فعّالة")
        form.addRow("الاسم:", self.name)
        form.addRow("الوصف:", self.desc)
        form.addRow("", self.active)
        self.editor.lay.addLayout(form)
        row = QHBoxLayout()
        save = button("حفظ", "check", "primary")
        save.clicked.connect(self.save)
        delete = button("حذف", "trash", "danger")
        delete.clicked.connect(self.delete)
        row.addWidget(save)
        row.addWidget(delete)
        row.addStretch(1)
        self.editor.lay.addLayout(row)
        right.addWidget(self.editor)

        self.assign_card = Card("جداول الدوام المسندة (الأحدث يتقدّم)")
        self.assign = DataTable([("schedule_name", "الجدول", "text"), ("start", "من", "date"),
                                 ("end", "إلى", "date"), ("note", "ملاحظة", "text")], stretch="schedule_name")
        self.assign.setMinimumHeight(160)
        self.assign_card.lay.addWidget(self.assign)
        arow = QHBoxLayout()
        self.sched = QComboBox()
        self.a_from = DateEdit()
        self.a_has_to = QCheckBox("حتى")
        self.a_to = DateEdit()
        self.a_to.setEnabled(False)
        self.a_has_to.toggled.connect(self.a_to.setEnabled)
        self.a_note = QLineEdit()
        self.a_note.setPlaceholderText("ملاحظة (مثلاً: دوام رمضان)")
        add = button("إسناد", "plus")
        add.clicked.connect(self.add_assignment)
        rm = button("حذف المحدد", "trash", "danger")
        rm.clicked.connect(self.remove_assignment)
        for w in (self.sched, label("من"), self.a_from, self.a_has_to, self.a_to):
            arow.addWidget(w)
        self.assign_card.lay.addLayout(arow)
        brow = QHBoxLayout()
        brow.addWidget(self.a_note, 1)
        brow.addWidget(add)
        brow.addWidget(rm)
        self.assign_card.lay.addLayout(brow)
        right.addWidget(self.assign_card)

        self.members_card = Card("الأعضاء الحاليون")
        self.members = DataTable([("code", "الرقم", "text"), ("full_name", "الاسم", "text")],
                                 stretch="full_name")
        self.members.setMinimumHeight(140)
        self.members_card.lay.addWidget(self.members)
        right.addWidget(self.members_card)
        body.addLayout(right, 3)
        self.lay.addLayout(body, 1)
        self.group_id = None

    def refresh(self):
        rows = []
        for g in self.db.groups.list(include_inactive=True):
            cur = self.db.groups.current_schedule(g["id"])
            rows.append(dict(g) | {"schedule": cur["name"] if cur else "— بلا جدول —",
                                   "state": "فعّالة" if g["active"] else "معطّلة",
                                   "_tones": {} if cur else {"schedule": "warn"},
                                   "_tone": "" if g["active"] else "muted"})
        self.table.set_rows(rows)
        self.sched.clear()
        for s in self.db.schedules.list(include_inactive=False):
            self.sched.addItem(s["name"], s["id"])
        if self.group_id is None and rows:
            self.group_id = rows[0]["id"]
        self._load()

    def _select(self):
        r = self.table.selected()
        if r:
            self.group_id = r["id"]
            self._load()

    def _load(self):
        has = self.group_id is not None
        self.assign_card.setEnabled(has)
        self.members_card.setEnabled(has)
        if not has:
            self.name.clear()
            self.desc.clear()
            self.active.setChecked(True)
            self.assign.set_rows([])
            self.members.set_rows([])
            return
        g = self.db.one("SELECT * FROM emp_groups WHERE id = ?", (self.group_id,))
        if g is None:
            self.group_id = None
            return self._load()
        self.name.setText(g["name"])
        self.desc.setPlainText(g["description"])
        self.active.setChecked(bool(g["active"]))
        self.assign.set_rows([dict(a) | {"start": parse_date(a["start_date"]), "end": parse_date(a["end_date"])}
                              for a in self.db.groups.assignments(self.group_id)])
        members = self.db.employees.list(group_id=self.group_id, active=True)
        self.members.set_rows([dict(m) for m in members])
        self.members_card.lay.itemAt(0).widget().setText(f"الأعضاء الحاليون ({digits(len(members))})")

    def new_group(self):
        self.group_id = None
        self._load()
        self.name.setFocus()

    def save(self):
        try:
            self.group_id = self.db.groups.save(self.name.text(), self.desc.toPlainText(), self.group_id,
                                                self.active.isChecked())
        except StoreError as exc:
            error(self, str(exc))
            return
        if not self.db.groups.assignments(self.group_id) and self.sched.count():
            self.db.groups.assign_schedule(self.group_id, self.sched.currentData(), "2000-01-01")
            self.toast("أُسند الجدول «%s» للمجموعة؛ يمكنك تغييره من الأسفل" % self.sched.currentText())
        else:
            self.toast("تم الحفظ", "ok")
        self.refresh()

    def delete(self):
        if self.group_id is None or not confirm(self, "حذف هذه المجموعة؟"):
            return
        try:
            self.db.groups.delete(self.group_id)
        except StoreError as exc:
            error(self, str(exc))
            return
        self.group_id = None
        self.refresh()

    def add_assignment(self):
        if self.group_id is None or self.sched.currentData() is None:
            return
        try:
            self.db.groups.assign_schedule(self.group_id, self.sched.currentData(), self.a_from.value(),
                                           self.a_to.value() if self.a_has_to.isChecked() else None,
                                           self.a_note.text().strip())
        except StoreError as exc:
            error(self, str(exc))
            return
        self.a_note.clear()
        self.toast("تم إسناد الجدول", "ok")
        self.refresh()

    def remove_assignment(self):
        r = self.assign.selected()
        if r is None:
            error(self, "اختر إسناداً من الجدول.")
            return
        if confirm(self, "حذف هذا الإسناد؟ سيُعاد حساب الأيام التي كان يغطيها وفق الإسنادات الأخرى."):
            self.db.schedules.delete_assignment(r["id"])
            self.refresh()

