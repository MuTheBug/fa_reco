"""Login and first-run dialogs."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from .. import APP_SUBTITLE, APP_TITLE
from ..db.seed import seed_defaults
from ..db.store import StoreError
from .theme import C, pixmap
from .widgets.common import error


class LoginDialog(QDialog):
    _failures = 0
    _locked_until = 0.0

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = None
        self.setWindowTitle("دخول الإدارة")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(12)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(pixmap("lock", C["primary"], 34))
        head.addWidget(ic)
        t = QLabel("دخول الإدارة")
        t.setObjectName("PageTitle")
        head.addWidget(t, 1)
        lay.addLayout(head)
        form = QFormLayout()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("اسم المستخدم:", self.username)
        form.addRow("كلمة المرور:", self.password)
        lay.addLayout(form)
        row = QHBoxLayout()
        ok = QPushButton("دخول")
        ok.setProperty("kind", "primary")
        ok.setDefault(True)
        ok.clicked.connect(self._login)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        self.username.setFocus()

    def _login(self):
        now = time.monotonic()
        if now < LoginDialog._locked_until:
            error(self, f"محاولات كثيرة خاطئة. انتظر {int(LoginDialog._locked_until - now)} ثانية.")
            return
        user = self.db.users.authenticate(self.username.text(), self.password.text())
        if user is None:
            LoginDialog._failures += 1
            if LoginDialog._failures >= 5:
                LoginDialog._locked_until = now + 60
                LoginDialog._failures = 0
            error(self, "اسم المستخدم أو كلمة المرور غير صحيحة.")
            self.password.clear()
            return
        LoginDialog._failures = 0
        self.user = user
        self.accept()


class FirstRunDialog(QDialog):
    """Create the organisation name and the first administrator."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle(f"{APP_TITLE} — الإعداد الأول")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        title = QLabel(f"مرحباً بك في «{APP_TITLE}»")
        title.setObjectName("PageTitle")
        lay.addWidget(title)
        sub = QLabel(f"{APP_SUBTITLE}. يعمل البرنامج وبياناته كلها على هذا الجهاز دون إنترنت.\n"
                     "أدخل اسم الدائرة وأنشئ حساب مدير النظام:")
        sub.setWordWrap(True)
        sub.setObjectName("PageSubtitle")
        lay.addWidget(sub)
        form = QFormLayout()
        self.org = QLineEdit()
        self.org.setPlaceholderText("مثال: دائرة صحة ...")
        self.subtitle = QLineEdit()
        self.subtitle.setPlaceholderText("اختياري: اسم القسم أو الجهة المرتبطة")
        self.full_name = QLineEdit()
        self.username = QLineEdit("admin")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password2 = QLineEdit()
        self.password2.setEchoMode(QLineEdit.Password)
        form.addRow("اسم الدائرة:", self.org)
        form.addRow("الجهة/القسم:", self.subtitle)
        form.addRow("اسم مدير النظام:", self.full_name)
        form.addRow("اسم المستخدم:", self.username)
        form.addRow("كلمة المرور:", self.password)
        form.addRow("تأكيد كلمة المرور:", self.password2)
        lay.addLayout(form)
        note = QLabel("احتفظ بكلمة المرور في مكان آمن؛ لا يمكن الدخول إلى الإدارة بدونها.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        ok = QPushButton("بدء الاستخدام")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self._create)
        lay.addWidget(ok, 0, Qt.AlignLeft)

    def _create(self):
        if not self.org.text().strip():
            error(self, "اكتب اسم الدائرة.")
            return
        if self.password.text() != self.password2.text():
            error(self, "كلمتا المرور غير متطابقتين.")
            return
        try:
            with self.db.tx():
                seed_defaults(self.db)
                self.db.users.create(self.username.text(), self.full_name.text() or "مدير النظام",
                                     self.password.text(), "admin")
                self.db.settings.set("org.name", self.org.text().strip(), audit=False)
                self.db.settings.set("org.subtitle", self.subtitle.text().strip(), audit=False)
        except StoreError as exc:
            error(self, str(exc))
            return
        self.db.users.authenticate(self.username.text(), self.password.text())
        self.accept()
