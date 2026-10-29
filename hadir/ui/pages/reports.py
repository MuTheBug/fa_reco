"""Ready-made reports, the custom report builder and saved reports."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                               QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QTabWidget,
                               QTableWidget, QVBoxLayout, QWidget)

from ...core.timeutil import MONTHS_LEVANT, digits
from ...reports import builtin, custom
from ...reports import sources as S
from ..admin import Page
from ..report_output import preview, save_excel, save_pdf
from ..theme import C, icon
from ..widgets.common import (Card, CheckList, DataTable, DateEdit, EmployeeCombo, PageHeader,
                              PeriodPicker, SpinBox, button, confirm, error, label)


class ScopeBox(QWidget):
    """Groups / employee filters shared by both report tabs."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.groups = CheckList()
        self.groups.setMaximumHeight(110)
        self.emp = EmployeeCombo(db, allow_all=True)
        self.inactive = QCheckBox("تضمين غير الفعّالين")
        form.addRow("المجموعات (بدون تحديد = الكل):", self.groups)
        form.addRow("الموظف:", self.emp)
        form.addRow("", self.inactive)

    def reload(self):
        checked = self.groups.checked()
        self.groups.set_items([(g["id"], g["name"]) for g in self.db.groups.list(include_inactive=True)],
                              checked)
        self.emp.reload()

    def params(self) -> dict:
        emp = self.emp.current_id()
        return {"group_ids": self.groups.checked() or None, "employee_ids": [emp] if emp else None,
                "include_inactive": self.inactive.isChecked()}

    def set_params(self, d: dict):
        self.groups.set_checked(d.get("group_ids") or [])
        emps = d.get("employee_ids") or []
        self.emp.set_id(emps[0] if emps else None)
        self.inactive.setChecked(bool(d.get("include_inactive")))


class ReportsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.lay.addWidget(PageHeader("التقارير", "تقارير جاهزة، أو اصنع تقريرك بنفسك واحفظه — طباعة A4 وتصدير Excel"))
        self.tabs = QTabWidget()
        self.lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._ready_tab(), icon("report", C["primary"], 16), "التقارير الجاهزة")
        self.builder = ReportBuilder(ctx, self)
        self.tabs.addTab(self.builder, icon("edit", C["primary"], 16), "اصنع تقريرك")

    # ------------------------------------------------------------- ready
    def _ready_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = Card("اختر التقرير")
        self.catalog = QListWidget()
        self.catalog.setMinimumWidth(300)
        for rid, title, desc, kind in builtin.CATALOG:
            it = QListWidgetItem(icon("report", C["primary"], 18), title)
            it.setData(Qt.UserRole, rid)
            it.setToolTip(desc)
            self.catalog.addItem(it)
        self.catalog.currentRowChanged.connect(self._choose)
        left.lay.addWidget(self.catalog, 1)
        left.lay.addWidget(label("تقاريري المحفوظة", "CardTitle"))
        self.saved = QListWidget()
        self.saved.setMaximumHeight(170)
        self.saved.itemDoubleClicked.connect(self._open_saved)
        left.lay.addWidget(self.saved)
        srow = QHBoxLayout()
        run = button("تشغيل", "eye")
        run.clicked.connect(lambda: self.saved.currentItem() and self._open_saved(self.saved.currentItem(), run=True))
        edit = button("تعديل", "edit")
        edit.clicked.connect(lambda: self.saved.currentItem() and self._open_saved(self.saved.currentItem()))
        rm = button("حذف", "trash", "danger")
        rm.clicked.connect(self._delete_saved)
        for b in (run, edit, rm):
            srow.addWidget(b)
        left.lay.addLayout(srow)
        lay.addWidget(left, 2)

        right = Card()
        self.r_title = label("", "PageTitle")
        self.r_desc = label("", "PageSubtitle", wrap=True)
        right.lay.addWidget(self.r_title)
        right.lay.addWidget(self.r_desc)
        form = QFormLayout()
        self.day = DateEdit()
        self.period = PeriodPicker("this_month")
        self.month = QComboBox()
        for i, m in enumerate(MONTHS_LEVANT, start=1):
            self.month.addItem(f"{m} ({digits(i)})", i)
        self.year = SpinBox(2000, 2100, self.db.now().year)
        self.year.setGroupSeparatorShown(False)
        self.row_day = ("اليوم:", self.day)
        form.addRow("اليوم:", self.day)
        form.addRow("الفترة:", self.period)
        mrow = QHBoxLayout()
        mrow.addWidget(self.month)
        mrow.addWidget(self.year)
        mrow.addStretch(1)
        self.month_w = QWidget()
        self.month_w.setLayout(mrow)
        form.addRow("الشهر/السنة:", self.month_w)
        self.form = form
        right.lay.addLayout(form)
        self.scope = ScopeBox(self.db)
        right.lay.addWidget(self.scope)
        self.opt_page = QCheckBox("كل موظف في صفحة مستقلة")
        self.opt_page.setChecked(True)
        self.opt_cancelled = QCheckBox("إظهار الإجازات الملغاة")
        self.opt_deleted = QCheckBox("إظهار البصمات المحذوفة")
        self.opt_bygroup = QCheckBox("تقسيم حسب المجموعة")
        self.opt_bygroup.setChecked(True)
        for o in (self.opt_page, self.opt_cancelled, self.opt_deleted, self.opt_bygroup):
            right.lay.addWidget(o)
        right.lay.addStretch(1)
        brow = QHBoxLayout()
        b1 = button("معاينة وطباعة", "print", "primary")
        b1.clicked.connect(lambda: self._run("preview"))
        b2 = button("حفظ PDF", "pdf")
        b2.clicked.connect(lambda: self._run("pdf"))
        b3 = button("تصدير Excel", "excel")
        b3.clicked.connect(lambda: self._run("excel"))
        for b in (b1, b2, b3):
            brow.addWidget(b)
        brow.addStretch(1)
        right.lay.addLayout(brow)
        lay.addWidget(right, 3)
        return w

    def refresh(self):
        self.scope.reload()
        today = self.db.now().date()
        self.day.set_value(today)
        self.month.setCurrentIndex(today.month - 1)
        self.year.setValue(today.year)
        if self.catalog.currentRow() < 0:
            self.catalog.setCurrentRow(0)
        self._reload_saved()
        self.builder.reload()

    def _reload_saved(self):
        self.saved.clear()
        for t in self.db.templates.list():
            it = QListWidgetItem(icon("report", C["ok"], 18), t["name"])
            it.setData(Qt.UserRole, t["id"])
            self.saved.addItem(it)

    def _choose(self, row: int):
        if row < 0:
            return
        rid, title, desc, kind = builtin.CATALOG[row]
        self.r_title.setText(title)
        self.r_desc.setText(desc)
        self.form.setRowVisible(0, kind == "day")
        self.form.setRowVisible(1, kind == "period")
        self.form.setRowVisible(2, kind in ("month", "year"))
        self.month.setVisible(kind == "month")
        self.opt_page.setVisible(rid == "timesheet")
        self.opt_cancelled.setVisible(rid == "leaves")
        self.opt_deleted.setVisible(rid == "punches")
        self.opt_bygroup.setVisible(rid == "monthly_summary")

    def _params(self, rid: str) -> dict:
        kind = next(c[3] for c in builtin.CATALOG if c[0] == rid)
        p = self.scope.params()
        if kind == "day":
            p.update(day=self.day.value(), start=self.day.value(), end=self.day.value())
        elif kind == "period":
            s, e = self.period.range()
            p.update(start=s, end=e)
        elif kind == "month":
            from ...core.timeutil import month_bounds

            s, e = month_bounds(self.year.value(), self.month.currentData())
            p.update(year=self.year.value(), month=self.month.currentData(), start=s, end=e)
        else:
            from datetime import date

            p.update(year=self.year.value(), start=date(self.year.value(), 1, 1),
                     end=date(self.year.value(), 12, 31))
        p.update(page_per_employee=self.opt_page.isChecked(), include_cancelled=self.opt_cancelled.isChecked(),
                 include_deleted=self.opt_deleted.isChecked(), by_group=self.opt_bygroup.isChecked())
        return p

    def _run(self, action: str):
        it = self.catalog.currentItem()
        if it is None:
            return
        rid = it.data(Qt.UserRole)
        try:
            rep = builtin.build(self.db, rid, self._params(rid))
        except Exception as exc:
            error(self, f"تعذّر إنشاء التقرير: {exc}")
            return
        {"preview": preview, "pdf": save_pdf, "excel": save_excel}[action](self, self.db, rep)

    def _open_saved(self, item, run: bool = False):
        definition = self.db.templates.get(item.data(Qt.UserRole))
        if definition is None:
            return
        self.builder.load(definition, item.data(Qt.UserRole), item.text())
        if run:
            self.builder.run("preview")
        else:
            self.tabs.setCurrentWidget(self.builder)

    def _delete_saved(self):
        it = self.saved.currentItem()
        if it and confirm(self, f"حذف التقرير المحفوظ «{it.text()}»؟"):
            self.db.templates.delete(it.data(Qt.UserRole))
            self._reload_saved()


class ReportBuilder(QWidget):
    def __init__(self, ctx, page: ReportsPage):
        super().__init__()
        self.ctx = ctx
        self.db = ctx.db
        self.page = page
        self.template_id = None
        lay = QHBoxLayout(self)
        left = QVBoxLayout()

        c1 = Card("١. مصدر البيانات والأعمدة")
        f1 = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("عنوان التقرير")
        self.source = QComboBox()
        for k, (t, _) in S.SOURCES.items():
            self.source.addItem(t, k)
        f1.addRow("العنوان:", self.title)
        f1.addRow("المصدر:", self.source)
        c1.lay.addLayout(f1)
        c1.lay.addWidget(label("ضع علامة على الأعمدة المطلوبة، ورتّبها بالأسهم، وانقر مرتين على العمود لتغيير عنوانه:",
                               "Hint", wrap=True))
        self.columns = QListWidget()
        self.columns.setDragDropMode(QAbstractItemView.InternalMove)
        self.columns.setMinimumHeight(230)
        c1.lay.addWidget(self.columns)
        arrows = QHBoxLayout()
        up = button("", "up")
        up.clicked.connect(lambda: self._move(-1))
        down = button("", "down")
        down.clicked.connect(lambda: self._move(1))
        arrows.addWidget(up)
        arrows.addWidget(down)
        arrows.addStretch(1)
        c1.lay.addLayout(arrows)
        left.addWidget(c1)
        lay.addLayout(left, 2)

        mid = QVBoxLayout()
        c2 = Card("٢. الفترة والنطاق")
        self.period = PeriodPicker("this_month")
        c2.lay.addWidget(self.period)
        self.scope = ScopeBox(self.db)
        c2.lay.addWidget(self.scope)
        self.status_lbl = label("الحالات (للسجل اليومي):")
        c2.lay.addWidget(self.status_lbl)
        self.statuses = CheckList([(k, v) for k, v in S.DAILY_STATUS_FILTERS.items()])
        self.statuses.setMaximumHeight(110)
        c2.lay.addWidget(self.statuses)
        mid.addWidget(c2)

        c3 = Card("٣. الشروط والترتيب والتجميع")
        c3.lay.addWidget(label("شروط إضافية (مثال: التأخير أكبر من 0:15):", "Hint"))
        self.conds = QTableWidget(0, 3)
        self.conds.setHorizontalHeaderLabels(["الحقل", "الشرط", "القيمة"])
        self.conds.horizontalHeader().setStretchLastSection(True)
        self.conds.verticalHeader().hide()
        self.conds.setMaximumHeight(130)
        c3.lay.addWidget(self.conds)
        crow = QHBoxLayout()
        addc = button("إضافة شرط", "plus")
        addc.clicked.connect(lambda: self._add_condition())
        rmc = button("حذف الشرط", "trash", "danger")
        rmc.clicked.connect(lambda: self.conds.removeRow(self.conds.currentRow()))
        crow.addWidget(addc)
        crow.addWidget(rmc)
        crow.addStretch(1)
        c3.lay.addLayout(crow)
        f3 = QFormLayout()
        self.group_by = QComboBox()
        self.sort1 = QComboBox()
        self.sort1_desc = QCheckBox("تنازلي")
        self.sort2 = QComboBox()
        self.sort2_desc = QCheckBox("تنازلي")
        s1 = QHBoxLayout()
        s1.addWidget(self.sort1, 1)
        s1.addWidget(self.sort1_desc)
        s2 = QHBoxLayout()
        s2.addWidget(self.sort2, 1)
        s2.addWidget(self.sort2_desc)
        self.totals = QCheckBox("إظهار المجاميع")
        self.totals.setChecked(True)
        self.landscape = QCheckBox("طباعة بالعرض (أفقي)")
        f3.addRow("تجميع حسب:", self.group_by)
        f3.addRow("الترتيب حسب:", s1)
        f3.addRow("ثم حسب:", s2)
        f3.addRow("", self.totals)
        f3.addRow("", self.landscape)
        c3.lay.addLayout(f3)
        mid.addWidget(c3)
        lay.addLayout(mid, 3)

        right = QVBoxLayout()
        c4 = Card("٤. النتيجة")
        self.result_info = label("", "Hint", wrap=True)
        c4.lay.addWidget(self.result_info)
        self.preview_table = DataTable([("x", "", "text")])
        self.preview_table.setMinimumHeight(260)
        c4.lay.addWidget(self.preview_table, 1)
        for text, ic, kind, act in (("عرض النتيجة", "eye", "", "table"), ("معاينة وطباعة", "print", "primary", "preview"),
                                    ("حفظ PDF", "pdf", "", "pdf"), ("تصدير Excel", "excel", "", "excel")):
            b = button(text, ic, kind)
            b.clicked.connect(lambda _=False, a=act: self.run(a))
            c4.lay.addWidget(b)
        save = button("حفظ التقرير لاستخدامه لاحقاً", "check")
        save.clicked.connect(self.save_template)
        c4.lay.addWidget(save)
        right.addWidget(c4)
        lay.addLayout(right, 2)

        self.source.currentIndexChanged.connect(self._source_changed)
        self.columns.itemDoubleClicked.connect(self._rename)
        self._source_changed()

    # --------------------------------------------------------------- data
    def reload(self):
        self.scope.reload()

    def _fields(self):
        return custom.fields_for(self.db, self.source.currentData())

    def _source_changed(self, *_):
        src = self.source.currentData()
        defaults = {
            "daily": ["date", "weekday", "code", "name", "check_in", "check_out", "late", "early", "status"],
            "summary": ["code", "name", "workdays", "present", "absent", "leave_days", "late", "overtime"],
            "punches": ["date", "time", "code", "name", "source", "score"],
            "leaves": ["code", "name", "type", "start", "end", "days", "reference"],
            "corrections": ["created_at", "username", "name", "action", "old_ts", "new_ts", "reason"],
            "employees": ["code", "name", "job_title", "phone", "group", "samples"],
        }[src]
        self._fill_columns([{"key": k} for k in defaults])
        fields = self._fields()
        for combo, blank in ((self.group_by, "بدون"), (self.sort1, "بدون"), (self.sort2, "بدون")):
            combo.clear()
            combo.addItem(blank, None)
            for k, t, _, _ in fields:
                combo.addItem(t, k)
        is_daily = src == "daily"
        self.statuses.setVisible(is_daily)
        self.status_lbl.setVisible(is_daily)
        self.period.setEnabled(src != "employees")
        self.conds.setRowCount(0)

    def _fill_columns(self, chosen: list[dict]):
        fields = self._fields()
        by_key = {f[0]: f for f in fields}
        self.columns.clear()
        order = [c["key"] for c in chosen if c["key"] in by_key] + [f[0] for f in fields
                                                                     if f[0] not in {c["key"] for c in chosen}]
        titles = {c["key"]: c.get("title") for c in chosen}
        chosen_keys = {c["key"] for c in chosen}
        for k in order:
            it = QListWidgetItem(titles.get(k) or by_key[k][1])
            it.setData(Qt.UserRole, k)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if k in chosen_keys else Qt.Unchecked)
            it.setToolTip(f"الحقل الأصلي: {by_key[k][1]}")
            self.columns.addItem(it)

    def _move(self, d: int):
        i = self.columns.currentRow()
        j = i + d
        if i < 0 or not (0 <= j < self.columns.count()):
            return
        it = self.columns.takeItem(i)
        self.columns.insertItem(j, it)
        self.columns.setCurrentRow(j)

    def _rename(self, it):
        text, ok = QInputDialog.getText(self, "عنوان العمود", "العنوان في التقرير:", text=it.text())
        if ok and text.strip():
            it.setText(text.strip())

    def _add_condition(self, cond: dict | None = None):
        r = self.conds.rowCount()
        self.conds.insertRow(r)
        f = QComboBox()
        for k, t, _, _ in self._fields():
            f.addItem(t, k)
        op = QComboBox()
        for k, t in custom.OPERATORS.items():
            op.addItem(t, k)
        val = QLineEdit()
        if cond:
            f.setCurrentIndex(max(0, f.findData(cond.get("field"))))
            op.setCurrentIndex(max(0, op.findData(cond.get("op"))))
            val.setText(str(cond.get("value") or ""))
        self.conds.setCellWidget(r, 0, f)
        self.conds.setCellWidget(r, 1, op)
        self.conds.setCellWidget(r, 2, val)

    def definition(self) -> dict:
        cols = []
        fields = {f[0]: f for f in self._fields()}
        for i in range(self.columns.count()):
            it = self.columns.item(i)
            if it.checkState() == Qt.Checked:
                k = it.data(Qt.UserRole)
                c = {"key": k}
                if it.text() != fields[k][1]:
                    c["title"] = it.text()
                cols.append(c)
        conds = []
        for r in range(self.conds.rowCount()):
            conds.append({"field": self.conds.cellWidget(r, 0).currentData(),
                          "op": self.conds.cellWidget(r, 1).currentData(),
                          "value": self.conds.cellWidget(r, 2).text()})
        sort = [{"field": c.currentData(), "desc": d.isChecked()}
                for c, d in ((self.sort1, self.sort1_desc), (self.sort2, self.sort2_desc)) if c.currentData()]
        d = {"title": self.title.text().strip(), "source": self.source.currentData(), "columns": cols,
             "period": self.period.spec(), "statuses": self.statuses.checked(), "conditions": conds,
             "group_by": self.group_by.currentData(), "sort": sort, "totals": self.totals.isChecked(),
             "landscape": self.landscape.isChecked()}
        d.update(self.scope.params())
        return d

    def load(self, d: dict, template_id=None, name: str = ""):
        self.template_id = template_id
        self.source.setCurrentIndex(max(0, self.source.findData(d.get("source", "daily"))))
        self.title.setText(d.get("title") or name)
        self._fill_columns(d.get("columns") or [])
        self.period.set_spec(d.get("period") or {"type": "this_month"})
        self.scope.reload()
        self.scope.set_params(d)
        self.statuses.set_checked(d.get("statuses") or [])
        self.conds.setRowCount(0)
        for c in d.get("conditions") or []:
            self._add_condition(c)
        self.group_by.setCurrentIndex(max(0, self.group_by.findData(d.get("group_by"))))
        sort = d.get("sort") or []
        for i, (c, desc) in enumerate(((self.sort1, self.sort1_desc), (self.sort2, self.sort2_desc))):
            if i < len(sort):
                c.setCurrentIndex(max(0, c.findData(sort[i]["field"])))
                desc.setChecked(bool(sort[i].get("desc")))
            else:
                c.setCurrentIndex(0)
        self.totals.setChecked(d.get("totals", True))
        self.landscape.setChecked(bool(d.get("landscape")))

    def run(self, action: str):
        d = self.definition()
        if not d["columns"]:
            error(self, "اختر عموداً واحداً على الأقل.")
            return
        try:
            rep = custom.run(self.db, d)
        except Exception as exc:
            error(self, f"تعذّر إنشاء التقرير: {exc}")
            return
        rows = list(rep.all_rows())
        self.result_info.setText(f"عدد الصفوف: {digits(len(rows))}")
        if action == "table":
            self.preview_table.model_.columns = [(c.key, c.title.replace("\n", " "), c.kind) for c in rep.columns]
            self.preview_table.set_rows(rows[:2000])
            return
        {"preview": preview, "pdf": save_pdf, "excel": save_excel}[action](self, self.db, rep)

    def save_template(self):
        d = self.definition()
        name, ok = QInputDialog.getText(self, "حفظ التقرير", "اسم التقرير المحفوظ:",
                                        text=d["title"] or "تقريري")
        if not ok or not name.strip():
            return
        self.template_id = self.db.templates.save(name.strip(), d, self.template_id)
        self.page._reload_saved()
        self.ctx.toast("تم حفظ التقرير ويظهر في «تقاريري المحفوظة»", "ok")
