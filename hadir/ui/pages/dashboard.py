"""Today at a glance."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel

from ...core import engine as E
from ...core.timeutil import digits, fmt_date_long
from ...reports import sources as S
from ..admin import Page
from ..theme import C
from ..widgets.common import Card, DataTable, PageHeader, SearchBox, StatTile, button, label


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
