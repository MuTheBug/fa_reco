"""The first-run dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .. import APP_SUBTITLE, APP_TITLE
from ..db.seed import seed_defaults
from ..db.store import StoreError
from .widgets.common import error


class FirstRunDialog(QDialog):
    """The organisation's name; the program has no passwords."""

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
                     "أدخل اسم الدائرة:")
        sub.setWordWrap(True)
        sub.setObjectName("PageSubtitle")
        lay.addWidget(sub)
        form = QFormLayout()
        self.org = QLineEdit()
        self.org.setPlaceholderText("مثال: دائرة صحة ...")
        self.subtitle = QLineEdit()
        self.subtitle.setPlaceholderText("اختياري: اسم القسم أو الجهة المرتبطة")
        form.addRow("اسم الدائرة:", self.org)
        form.addRow("الجهة/القسم:", self.subtitle)
        lay.addLayout(form)
        ok = QPushButton("بدء الاستخدام")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self._create)
        lay.addWidget(ok, 0, Qt.AlignLeft)

    def _create(self):
        if not self.org.text().strip():
            error(self, "اكتب اسم الدائرة.")
            return
        try:
            with self.db.tx():
                seed_defaults(self.db)
                self.db.settings.set("org.name", self.org.text().strip(), audit=False)
                self.db.settings.set("org.subtitle", self.subtitle.text().strip(), audit=False)
        except StoreError as exc:
            error(self, str(exc))
            return
        self.db.users.enter()
        self.accept()
