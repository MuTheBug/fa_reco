"""Administration shell: sidebar navigation and pages."""

from __future__ import annotations

import importlib

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_TITLE
from ..core.security import ROLES, can
from .theme import icon
from .widgets.common import Toast

# key, Arabic title, icon, module:class, permission
PAGES = [
    ("dashboard", "لوحة اليوم", "home", "dashboard:DashboardPage", "view"),
    ("employees", "الموظفون", "users", "employees:EmployeesPage", "edit"),
    ("groups", "المجموعات", "layers", "groups:GroupsPage", "edit"),
    ("schedules", "جداول الدوام", "schedule", "schedules:SchedulesPage", "edit"),
    ("leaves", "الإجازات", "leave", "leaves:LeavesPage", "edit"),
    ("holidays", "العطل الرسمية", "holiday", "holidays:HolidaysPage", "edit"),
    ("timeline", "السجل الزمني", "clock", "timeline:TimelinePage", "view"),
    ("reports", "التقارير", "report", "reports:ReportsPage", "view"),
    ("logs", "السجلات والأمان", "shield", "logs:LogsPage", "edit"),
    ("diagnostics", "اختبار التعرّف", "face", "diagnostics:DiagnosticsPage", "enroll"),
    ("settings", "الإعدادات", "settings", "settings:SettingsPage", "settings"),
]


class Page(QWidget):
    """Base class: pages get the shared context and are refreshed when shown."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.db = ctx.db
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(26, 22, 26, 22)
        self.lay.setSpacing(14)

    def refresh(self):
        pass

    def leave(self):
        """Called when navigating away (stop cameras etc.)."""

    def can(self, permission: str) -> bool:
        return can(self.db.user.role if self.db.user else None, permission)

    def toast(self, text: str, tone: str = ""):
        self.ctx.toast(text, tone)


class AdminShell(QWidget):
    kiosk_requested = Signal()
    logout_requested = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 20, 14, 16)
        sl.setSpacing(4)
        t = QLabel(APP_TITLE)
        t.setObjectName("SidebarTitle")
        sl.addWidget(t)
        self.org = QLabel()
        self.org.setObjectName("SidebarSub")
        self.org.setWordWrap(True)
        sl.addWidget(self.org)
        sl.addSpacing(14)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, title, ic, _, _ in PAGES:
            b = QPushButton(f"  {title}")
            b.setIcon(icon(ic, "#dbe5f1", 19))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.group.addButton(b)
            self.buttons[key] = b
            sl.addWidget(b)
        sl.addStretch(1)
        self.user_lbl = QLabel()
        self.user_lbl.setObjectName("SidebarUser")
        self.user_lbl.setWordWrap(True)
        sl.addWidget(self.user_lbl)
        kiosk = QPushButton("  شاشة الحضور")
        kiosk.setIcon(icon("kiosk", "#dbe5f1", 19))
        kiosk.clicked.connect(self.kiosk_requested.emit)
        sl.addWidget(kiosk)
        out = QPushButton("  تسجيل الخروج")
        out.setIcon(icon("logout", "#dbe5f1", 19))
        out.clicked.connect(self.logout_requested.emit)
        sl.addWidget(out)
        lay.addWidget(side)

        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.pages: dict[str, Page] = {}
        self.current: str | None = None
        self.toaster = Toast(self)

    def prepare(self):
        """Apply the signed-in user's permissions; called after each login."""
        db = self.ctx.db
        role = db.user.role if db.user else None
        for key, _, _, _, perm in PAGES:
            self.buttons[key].setVisible(can(role, perm))
        self.org.setText(db.settings.get("org.name"))
        if db.user:
            self.user_lbl.setText(f"{db.user.full_name or db.user.username}\n{ROLES.get(role, role)}")
        for page in self.pages.values():
            page.deleteLater()
        self.pages.clear()
        self.current = None
        self.show_page("dashboard")

    def page(self, key: str) -> Page:
        if key not in self.pages:
            spec = next(p for p in PAGES if p[0] == key)
            mod_name, cls_name = spec[3].split(":")
            mod = importlib.import_module(f"hadir.ui.pages.{mod_name}")
            page = getattr(mod, cls_name)(self.ctx)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            scroll.setFrameShape(QFrame.NoFrame)
            self.stack.addWidget(scroll)
            page._scroll = scroll
            self.pages[key] = page
        return self.pages[key]

    def show_page(self, key: str, **kwargs):
        if self.current and self.current in self.pages:
            self.pages[self.current].leave()
        page = self.page(key)
        self.current = key
        self.buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page._scroll)
        if kwargs and hasattr(page, "open_with"):
            page.open_with(**kwargs)
        else:
            page.refresh()

    def leave(self):
        if self.current and self.current in self.pages:
            self.pages[self.current].leave()
