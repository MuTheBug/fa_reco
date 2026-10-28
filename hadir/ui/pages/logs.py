"""Security events and the audit log."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from ...core.timeutil import parse_dt
from ...db.store import EVENT_LABELS
from ...paths import snapshots_dir
from ..admin import Page
from ..theme import C
from ..widgets.common import DataTable, PageHeader, PeriodPicker, SearchBox, button, label


def _details(text: str) -> str:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(obj, dict):
        return "، ".join(f"{k}: {v}" for k, v in obj.items())
    return str(obj)


class LogsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.lay.addWidget(PageHeader("السجلات والأمان", "كل عملية في البرنامج مسجلة باسم المستخدم ووقتها"))
        self.tabs = QTabWidget()
        self.lay.addWidget(self.tabs, 1)

        w = QWidget()
        lay = QVBoxLayout(w)
        f = QHBoxLayout()
        self.ev_period = PeriodPicker("this_month")
        self.ev_kind = QComboBox()
        self.ev_kind.addItem("كل الأحداث", None)
        for k, v in EVENT_LABELS.items():
            self.ev_kind.addItem(v, k)
        seen = button("تعليم الكل كمقروء", "check")
        seen.clicked.connect(self._mark_seen)
        f.addWidget(self.ev_period)
        f.addWidget(self.ev_kind)
        f.addStretch(1)
        f.addWidget(seen)
        lay.addLayout(f)
        body = QHBoxLayout()
        self.events = DataTable([("time", "الوقت", "datetime"), ("kind_ar", "الحدث", "text"),
                                 ("employee", "الموظف المعني", "text"), ("details", "التفاصيل", "text")],
                                stretch="details")
        self.events.clicked.connect(lambda *_: self._show_event())
        body.addWidget(self.events, 3)
        self.ev_img = QLabel(alignment=Qt.AlignCenter)
        self.ev_img.setMinimumWidth(260)
        self.ev_img.setStyleSheet(f"background: {C['muted_bg']}; border-radius: 10px;")
        body.addWidget(self.ev_img, 1)
        lay.addLayout(body, 1)
        self.tabs.addTab(w, "أحداث الأمان")

        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        f2 = QHBoxLayout()
        self.au_period = PeriodPicker("this_month")
        self.au_search = SearchBox()
        f2.addWidget(self.au_period)
        f2.addStretch(1)
        f2.addWidget(self.au_search)
        l2.addLayout(f2)
        self.audit = DataTable([("time", "الوقت", "datetime"), ("username", "المستخدم", "text"),
                                ("action", "العملية", "text"), ("details", "التفاصيل", "text")],
                               stretch="details")
        l2.addWidget(self.audit, 1)
        l2.addWidget(label("سجل العمليات للاطلاع فقط ولا يمكن تعديله من داخل البرنامج.", "Hint"))
        self.tabs.addTab(w2, "سجل العمليات")

        for sig in (self.ev_period.changed, self.ev_kind.currentIndexChanged, self.au_period.changed,
                    self.au_search.textChanged, self.tabs.currentChanged):
            sig.connect(self.refresh)

    def refresh(self, *_):
        names = self.db.employees.names()
        s, e = self.ev_period.range()
        rows = []
        for ev in self.db.events.list(s, e, self.ev_kind.currentData()):
            rows.append({"time": parse_dt(ev["ts"]), "kind_ar": EVENT_LABELS.get(ev["kind"], ev["kind"]),
                         "employee": names.get(ev["employee_id"], ""), "details": ev["details"],
                         "snapshot": ev["snapshot"],
                         "_tone": "bad" if ev["kind"] in ("spoof", "clock_back", "clock_jump") else
                         ("warn" if not ev["seen"] else "")})
        self.events.set_rows(rows)
        s2, e2 = self.au_period.range()
        self.audit.set_rows([{"time": parse_dt(a["ts"]), "username": a["username"], "action": a["action"],
                              "details": _details(a["details"])}
                             for a in self.db.audit_log.list(s2, e2, self.au_search.text().strip())])

    def _show_event(self):
        r = self.events.selected()
        self.ev_img.clear()
        if r and r.get("snapshot"):
            pm = QPixmap(str(snapshots_dir() / r["snapshot"]))
            if not pm.isNull():
                self.ev_img.setPixmap(pm.scaledToWidth(240, Qt.SmoothTransformation))
                return
        self.ev_img.setText("لا توجد صورة")

    def _mark_seen(self):
        self.db.events.mark_all_seen()
        self.refresh()
        self.toast("تم التعليم كمقروء")
