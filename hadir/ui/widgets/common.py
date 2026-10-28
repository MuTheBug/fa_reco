"""Reusable widgets and helpers."""

from __future__ import annotations

from datetime import date, datetime, time

from PySide6.QtCore import (QAbstractTableModel, QDate, QLocale, QModelIndex, QSortFilterProxyModel,
                            Qt, QTime, QTimer, Signal)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDateEdit, QDialog,
                               QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSpinBox, QTableView, QTimeEdit, QVBoxLayout,
                               QWidget)

from ...core.timeutil import Fmt, digits, month_bounds
from ...reports.model import format_value
from ...reports.sources import PERIODS, resolve_period
from ..theme import C, TONE_BG, TONE_FG, icon

AR_LOCALE = QLocale(QLocale.Arabic, QLocale.Iraq)


def digit_locale() -> QLocale:
    return AR_LOCALE if Fmt.arabic_digits else QLocale(QLocale.English, QLocale.UnitedKingdom)


def button(text: str, icon_name: str | None = None, kind: str = "", tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon_name:
        color = "white" if kind == "primary" else (C["bad"] if kind == "danger" else C["primary"])
        b.setIcon(icon(icon_name, color, 18))
    if tooltip:
        b.setToolTip(tooltip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class Card(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(10)
        if title:
            self.lay.addWidget(label(title, "CardTitle"))


class StatTile(QFrame):
    def __init__(self, title: str, color: str, icon_name: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        text = QVBoxLayout()
        self.value = label("—", "StatValue")
        self.value.setStyleSheet(f"color: {color};")
        text.addWidget(self.value)
        text.addWidget(label(title, "StatLabel"))
        lay.addLayout(text, 1)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(icon(icon_name, color, 30).pixmap(30, 30))
            ic.setStyleSheet(f"background: {color}1a; border-radius: 12px; padding: 10px;")
            lay.addWidget(ic, 0, Qt.AlignTop)

    def set(self, value) -> None:
        self.value.setText(digits(value))


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "PageTitle"))
        self.subtitle = label(subtitle, "PageSubtitle")
        if subtitle:
            text.addWidget(self.subtitle)
        lay.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay.addLayout(self.actions)

    def add(self, w: QWidget) -> QWidget:
        self.actions.addWidget(w)
        return w


def badge(text: str, tone: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Badge")
    lb.setStyleSheet(f"background: {TONE_BG.get(tone, C['muted_bg'])}; color: {TONE_FG.get(tone, C['text'])};")
    return lb


# ---------------------------------------------------------------------------
class DictTableModel(QAbstractTableModel):
    """Rows are dicts; columns are (key, title, kind) tuples (kind as in report columns)."""

    def __init__(self, columns: list[tuple], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def row(self, i: int) -> dict:
        return self.rows[i]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section][1]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        key, _, kind = self.columns[index.column()][:3]
        v = r.get(key)
        if role == Qt.DisplayRole:
            return format_value(v, kind) if kind != "raw" else ("" if v is None else str(v))
        if role == Qt.UserRole:  # sort key
            if v is None:
                return ""
            if isinstance(v, (datetime, date, time)):
                return v.isoformat()
            return v
        if role == Qt.TextAlignmentRole:
            if kind in ("int", "minutes", "float", "percent", "time", "date", "datetime", "hours"):
                return int(Qt.AlignCenter)
            return int(Qt.AlignRight | Qt.AlignVCenter)
        tone = (r.get("_tones") or {}).get(key) or r.get("_tone")
        if role == Qt.BackgroundRole and tone in TONE_BG:
            return QColor(TONE_BG[tone])
        if role == Qt.ForegroundRole and tone == "muted":
            return QColor(C["muted"])
        if role == Qt.ToolTipRole and r.get("_tip"):
            return r["_tip"]
        return None


class SortProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)

    def lessThan(self, left, right):
        a, b = left.data(Qt.UserRole), right.data(Qt.UserRole)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class DataTable(QTableView):
    activated_row = Signal(dict)

    def __init__(self, columns: list[tuple], parent=None, stretch: str | None = None):
        super().__init__(parent)
        self.model_ = DictTableModel(columns, self)
        self.proxy = SortProxy(self)
        self.proxy.setSourceModel(self.model_)
        self.setModel(self.proxy)
        # Keep the order the rows were given in until the user clicks a column header.
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(34)
        self.setShowGrid(False)
        self.setWordWrap(False)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setMinimumSectionSize(44)  # the style sheet would otherwise force ~130 px per column
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setStretchLastSection(False)
        self._stretch = [c[0] for c in columns].index(stretch) if stretch else None
        if self._stretch is not None:
            h.setSectionResizeMode(self._stretch, QHeaderView.Stretch)
        self.doubleClicked.connect(self._on_double)

    def set_rows(self, rows: list[dict]) -> None:
        self.setSortingEnabled(False)
        self.model_.set_rows(rows)
        self.setSortingEnabled(True)

    def selected(self) -> dict | None:
        idx = self.currentIndex()
        if not idx.isValid() or not self.selectionModel().hasSelection():
            return None
        return self.model_.row(self.proxy.mapToSource(idx).row())

    def _on_double(self, idx):
        if idx.isValid():
            self.activated_row.emit(self.model_.row(self.proxy.mapToSource(idx).row()))

    def filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "بحث…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icon("search", C["muted"], 16), QLineEdit.LeadingPosition)
        self.setMinimumWidth(220)


# ---------------------------------------------------------------------------
class DateEdit(QDateEdit):
    def __init__(self, value: date | None = None, parent=None):
        super().__init__(parent)
        # QDateTimeEdit reverses its sections when the display format is set on a
        # right-to-left widget (08:00 would read 00:08), so switch to left-to-right first.
        # Numbers are read left-to-right in Arabic too; the calendar popup stays RTL.
        self.setLayoutDirection(Qt.LeftToRight)
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy/MM/dd")
        self.setLocale(digit_locale())
        self.calendarWidget().setLayoutDirection(Qt.RightToLeft)
        self.calendarWidget().setLocale(AR_LOCALE)
        self.calendarWidget().setFirstDayOfWeek(Qt.Sunday)
        self.setDate(QDate(value or date.today()))

    def value(self) -> date:
        d = self.date()
        return date(d.year(), d.month(), d.day())

    def set_value(self, d: date) -> None:
        self.setDate(QDate(d))


class TimeEdit(QTimeEdit):
    def __init__(self, value: time | None = None, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LeftToRight)  # before the format: see DateEdit
        self.setDisplayFormat("HH:mm")
        self.setLocale(digit_locale())
        if value:
            self.setTime(QTime(value.hour, value.minute))

    def value(self) -> time:
        t = self.time()
        return time(t.hour(), t.minute())

    def set_value(self, t: time) -> None:
        self.setTime(QTime(t.hour, t.minute))


class SpinBox(QSpinBox):
    def __init__(self, lo: int = 0, hi: int = 999, value: int = 0, suffix: str = "", parent=None):
        super().__init__(parent)
        self.setRange(lo, hi)
        self.setValue(value)
        self.setLocale(digit_locale())
        if suffix:
            self.setSuffix(suffix)


class PeriodPicker(QWidget):
    """Preset periods (today, this month…) or a custom from/to range."""

    changed = Signal()

    def __init__(self, default: str = "this_month", parent=None, presets=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        for k in presets or PERIODS:
            self.combo.addItem(PERIODS[k], k)
        self.start = DateEdit()
        self.end = DateEdit()
        lay.addWidget(self.combo)
        lay.addWidget(label("من"))
        lay.addWidget(self.start)
        lay.addWidget(label("إلى"))
        lay.addWidget(self.end)
        self.combo.setCurrentIndex(max(0, self.combo.findData(default)))
        self.combo.currentIndexChanged.connect(self._preset)
        self.start.dateChanged.connect(self._manual)
        self.end.dateChanged.connect(self._manual)
        self._busy = False
        self._preset()

    def _preset(self):
        kind = self.combo.currentData()
        if kind != "custom":
            s, e = resolve_period({"type": kind}, date.today())
            self._busy = True
            self.start.set_value(s)
            self.end.set_value(e)
            self._busy = False
        self.changed.emit()

    def _manual(self):
        if self._busy:
            return
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(self.combo.findData("custom"))
        self.combo.blockSignals(False)
        self.changed.emit()

    def range(self) -> tuple[date, date]:
        s, e = self.start.value(), self.end.value()
        return (s, e) if s <= e else (e, s)

    def spec(self) -> dict:
        kind = self.combo.currentData()
        if kind == "custom":
            s, e = self.range()
            return {"type": "custom", "start": s.isoformat(), "end": e.isoformat()}
        return {"type": kind}

    def set_spec(self, spec: dict) -> None:
        kind = (spec or {}).get("type", "this_month")
        if kind == "custom":
            self.combo.setCurrentIndex(self.combo.findData("custom"))
            self.start.set_value(date.fromisoformat(spec["start"]))
            self.end.set_value(date.fromisoformat(spec["end"]))
        else:
            self.combo.setCurrentIndex(max(0, self.combo.findData(kind)))

    def set_month(self, year: int, month: int) -> None:
        s, e = month_bounds(year, month)
        self.set_spec({"type": "custom", "start": s.isoformat(), "end": e.isoformat()})


class EmployeeCombo(QComboBox):
    """Searchable employee selector (by name or code)."""

    def __init__(self, db, allow_all: bool = False, include_inactive: bool = True, parent=None):
        super().__init__(parent)
        self.db = db
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setPlaceholderText("اكتب الاسم أو الرقم الوظيفي…")
        self.allow_all = allow_all
        self.include_inactive = include_inactive
        self.setMinimumWidth(260)
        self.reload()

    def reload(self):
        current = self.current_id()
        self.blockSignals(True)
        self.clear()
        if self.allow_all:
            self.addItem("جميع الموظفين", None)
        for e in self.db.employees.list(active=None if self.include_inactive else True):
            suffix = "" if e["active"] else " (غير فعّال)"
            self.addItem(f"{e['full_name']} — {digits(e['code'])}{suffix}", e["id"])
        comp = QCompleter(self.model(), self)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompleter(comp)
        if current is not None:
            self.set_id(current)
        self.blockSignals(False)

    def current_id(self) -> int | None:
        i = self.currentIndex()
        if i < 0:
            return None
        text = self.currentText().strip()
        if text and text != self.itemText(i):
            j = self.findText(text, Qt.MatchContains)
            return self.itemData(j) if j >= 0 else None
        return self.itemData(i)

    def set_id(self, emp_id: int | None) -> None:
        i = self.findData(emp_id)
        if i >= 0:
            self.setCurrentIndex(i)


class CheckList(QListWidget):
    """A compact list of checkable items (e.g. groups)."""

    changed = Signal()

    def __init__(self, items: list[tuple] = (), parent=None):
        super().__init__(parent)
        self.itemChanged.connect(lambda *_: self.changed.emit())
        self.set_items(items)

    def set_items(self, items: list[tuple], checked=()) -> None:
        self.blockSignals(True)
        self.clear()
        for value, text in items:
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, value)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if value in checked else Qt.Unchecked)
            self.addItem(it)
        self.blockSignals(False)

    def checked(self) -> list:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())
                if self.item(i).checkState() == Qt.Checked]

    def set_checked(self, values) -> None:
        self.blockSignals(True)
        for i in range(self.count()):
            it = self.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in values else Qt.Unchecked)
        self.blockSignals(False)


# ---------------------------------------------------------------------------
QUICK_REASONS = ["نسي تسجيل البصمة", "عطل في الجهاز أو الكاميرا", "واجب رسمي خارج الدائرة",
                 "بصمة مكررة أو خاطئة", "بموافقة المدير المباشر"]


class ReasonDialog(QDialog):
    """Ask for the written reason that every correction requires."""

    def __init__(self, title: str, summary: str = "", parent=None, quick=QUICK_REASONS):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        if summary:
            lay.addWidget(label(summary, wrap=True))
        lay.addWidget(label("سبب التعديل (إلزامي — يُحفظ في سجل التعديلات ولا يمكن حذفه):", wrap=True))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("اكتب السبب بوضوح، ومرجع الموافقة إن وجد…")
        self.text.setFixedHeight(90)
        lay.addWidget(self.text)
        if quick:
            row = QHBoxLayout()
            row.setSpacing(6)
            for q in quick:
                b = QPushButton(q)
                b.setProperty("kind", "ghost")
                b.clicked.connect(lambda _=False, t=q: self._append(t))
                row.addWidget(b)
            row.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(row)
            lay.addWidget(wrap)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("حفظ")
        bb.button(QDialogButtonBox.Cancel).setText("إلغاء")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _append(self, t: str):
        cur = self.text.toPlainText().strip()
        self.text.setPlainText(f"{cur} — {t}" if cur else t)

    def _accept(self):
        if len(self.reason()) < 3:
            error(self, "اكتب سبباً واضحاً قبل الحفظ.")
            return
        self.accept()

    def reason(self) -> str:
        return self.text.toPlainText().strip()

    @classmethod
    def ask(cls, parent, title: str, summary: str = "") -> str | None:
        dlg = cls(title, summary, parent)
        return dlg.reason() if dlg.exec() == QDialog.Accepted else None


def _box(parent, icon_kind, title, text, buttons=QMessageBox.Ok):
    box = QMessageBox(icon_kind, title, text, buttons, parent)
    box.setLayoutDirection(Qt.RightToLeft)
    names = {QMessageBox.Ok: "حسناً", QMessageBox.Yes: "نعم", QMessageBox.No: "لا",
             QMessageBox.Cancel: "إلغاء"}
    for b, t in names.items():
        btn = box.button(b)
        if btn:
            btn.setText(t)
    return box.exec()


def error(parent, text: str, title: str = "تنبيه") -> None:
    _box(parent, QMessageBox.Warning, title, text)


def info(parent, text: str, title: str = "معلومة") -> None:
    _box(parent, QMessageBox.Information, title, text)


def confirm(parent, text: str, title: str = "تأكيد") -> bool:
    return _box(parent, QMessageBox.Question, title, text,
                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes


class Toast(QLabel):
    """A short message that fades out at the bottom of a window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(f"background: {C['navy']}; color: white; border-radius: 10px;"
                           " padding: 10px 18px;")
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, ms: int = 2600, tone: str = "") -> None:
        bg = {"bad": C["bad"], "ok": C["ok"], "warn": C["warn"]}.get(tone, C["navy"])
        self.setStyleSheet(f"background: {bg}; color: white; border-radius: 10px; padding: 10px 18px;")
        self.setText(text)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._timer.start(ms)


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {C['border']};")
    return f


def expanding() -> QWidget:
    w = QWidget()
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w
