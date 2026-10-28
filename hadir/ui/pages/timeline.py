"""Attendance timeline per employee, with corrections that require a written reason."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QPlainTextEdit, QPushButton, QStyledItemDelegate, QVBoxLayout)

from ...core import engine as E
from ...core.timeutil import digits, fmt_date, fmt_time, parse_dt
from ...db.store import StoreError
from ...paths import snapshots_dir
from ...reports import sources as S
from ..admin import Page
from ..theme import C
from ..widgets.common import (QUICK_REASONS, Card, DataTable, DateEdit, EmployeeCombo, PageHeader,
                              PeriodPicker, ReasonDialog, TimeEdit, button, error, label)

KIND_CHOICES = [("auto", "تلقائي (يحدده البرنامج)"), ("in", "حضور"), ("out", "انصراف")]


class TimelineDelegate(QStyledItemDelegate):
    """Paints a 24-hour bar (04:00 → 04:00) with the shift, presence, punches and leaves."""

    def paint(self, painter: QPainter, option, index):
        model = index.model()
        src = model.mapToSource(index) if hasattr(model, "mapToSource") else index
        row = src.model().row(src.row())
        rec: E.DayRecord = row.get("_record")
        super().paint(painter, option, index)
        if rec is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(6, 9, -6, -9)
        start = datetime.combine(rec.date, datetime.min.time()) + timedelta(hours=4)
        span = 24 * 3600.0

        def x(ts: datetime) -> float:
            f = min(1.0, max(0.0, (ts - start).total_seconds() / span))
            return r.right() - f * r.width()  # right-to-left time axis

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#eef2f7"))
        painter.drawRoundedRect(r, 4, 4)
        if rec.shift is not None and rec.status not in (E.HOLIDAY,):
            a, b = x(rec.shift.start), x(rec.shift.end)
            painter.setBrush(QColor("#cfe0f5"))
            painter.drawRect(QRectF(min(a, b), r.top(), abs(a - b), r.height()))
        for lv in rec.hourly_leaves:
            s, e = lv.interval()
            a, b = x(s), x(e)
            painter.setBrush(QColor("#c7d2fe"))
            painter.drawRect(QRectF(min(a, b), r.top() + r.height() * 0.6, abs(a - b), r.height() * 0.4))
        ps = [p.ts for p in rec.punches]
        if len(ps) >= 2:
            pairs = [(ps[i], ps[i + 1]) for i in range(0, len(ps) - 1, 2)] if len(ps) % 2 == 0 else [(ps[0], ps[-1])]
            painter.setBrush(QColor("#4ade80"))
            for s, e in pairs:
                a, b = x(s), x(e)
                painter.drawRect(QRectF(min(a, b), r.top() + r.height() * 0.25, abs(a - b), r.height() * 0.5))
        painter.setPen(QPen(QColor("#0f2c4c"), 2))
        for t in ps:
            xx = x(t)
            painter.drawLine(int(xx), int(r.top()) - 2, int(xx), int(r.bottom()) + 2)
        # hour ticks every 4 hours
        painter.setPen(QPen(QColor("#94a3b8"), 1))
        for h in range(0, 25, 4):
            xx = r.right() - h / 24 * r.width()
            painter.drawLine(int(xx), int(r.bottom()), int(xx), int(r.bottom()) + 3)
        painter.restore()


class TimelinePage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        header = PageHeader("السجل الزمني", "تفاصيل دوام الموظف يوماً بيوم، وتصحيح البصمات مع كتابة السبب")
        self.lay.addWidget(header)
        tools = QHBoxLayout()
        self.emp = EmployeeCombo(self.db)
        self.period = PeriodPicker("this_month")
        tools.addWidget(label("الموظف:"))
        tools.addWidget(self.emp, 1)
        tools.addWidget(self.period)
        self.lay.addLayout(tools)
        self.summary = label("", wrap=True)
        self.summary.setStyleSheet(f"background: white; border: 1px solid {C['border']}; border-radius: 10px;"
                                   " padding: 8px 12px;")
        self.lay.addWidget(self.summary)

        days_card = Card("الأيام")
        self.days = DataTable([
            ("date", "التاريخ", "date"), ("weekday", "اليوم", "text"), ("shift", "الدوام", "text"),
            ("check_in", "الحضور", "time"), ("check_out", "الانصراف", "time"), ("late", "تأخير", "minutes"),
            ("early", "مبكر", "minutes"), ("worked", "العمل", "minutes"), ("bar", "المخطط الزمني لليوم", "raw"),
            ("status", "الحالة", "text"), ("notes", "ملاحظات", "text")], stretch="bar")
        self.days.setItemDelegateForColumn(8, TimelineDelegate(self.days))
        self.days.horizontalHeader().setSectionResizeMode(8, QHeaderView.Fixed)
        self.days.horizontalHeader().resizeSection(8, 320)
        self.days.setMinimumHeight(300)
        self.days.clicked.connect(lambda *_: self._select_day())
        days_card.lay.addWidget(self.days)
        legend = label("المخطط من ٤ صباحاً إلى ٤ صباح اليوم التالي (من اليمين إلى اليسار) — ■ أزرق فاتح: وقت الدوام   ■ أخضر: مدة التواجد   | خط داكن: بصمة   ■ بنفسجي: إجازة زمنية",
                       "Hint")
        days_card.lay.addWidget(legend)
        self.lay.addWidget(days_card, 3)

        bottom = QHBoxLayout()
        side = Card("بصمات اليوم المحدد")
        self.day_title = label("", "CardTitle")
        side.lay.addWidget(self.day_title)
        inner = QHBoxLayout()
        left = QVBoxLayout()
        self.punches = DataTable([("time", "الوقت", "datetime"), ("source", "المصدر", "text"),
                                  ("kind", "النوع", "text"), ("score", "المطابقة", "float"),
                                  ("state", "الحالة", "text")], stretch="state")
        self.punches.setMinimumHeight(150)
        self.punches.clicked.connect(lambda *_: self._show_snapshot())
        left.addWidget(self.punches)
        grid = QHBoxLayout()
        self.btn_add = button("إضافة بصمة", "plus", "primary")
        self.btn_add.clicked.connect(self.add_punch)
        self.btn_edit = button("تعديل الوقت", "edit")
        self.btn_edit.clicked.connect(self.edit_punch)
        self.btn_del = button("حذف", "trash", "danger")
        self.btn_del.clicked.connect(self.delete_punch)
        self.btn_restore = button("استرجاع", "refresh")
        self.btn_restore.clicked.connect(self.restore_punch)
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_restore):
            grid.addWidget(b)
        grid.addStretch(1)
        left.addLayout(grid)
        inner.addLayout(left, 3)
        self.snapshot = QLabel(alignment=Qt.AlignCenter)
        self.snapshot.setFixedSize(170, 170)
        self.snapshot.setWordWrap(True)
        self.snapshot.setStyleSheet(f"background: {C['muted_bg']}; border-radius: 10px; color: {C['muted']};")
        inner.addWidget(self.snapshot)
        side.lay.addLayout(inner)
        side.lay.addWidget(label("كل تعديل يتطلب سبباً مكتوباً ويُحفظ في سجل التعديلات مع اسم المستخدم"
                                 " والوقت، ولا يمكن حذفه.", "Hint", wrap=True))
        bottom.addWidget(side, 3)

        corr = Card("سجل التعديلات خلال الفترة")
        self.corr = DataTable([(k, t, kind) for k, t, kind, _ in S.CORRECTION_FIELDS
                               if k not in ("code", "name")], stretch="reason")
        self.corr.setMinimumHeight(150)
        corr.lay.addWidget(self.corr)
        bottom.addWidget(corr, 2)
        self.lay.addLayout(bottom, 2)

        self.emp.currentIndexChanged.connect(self.reload)
        self.period.changed.connect(self.reload)
        self.selected_date = None

    def open_with(self, employee_id=None, **_):
        self.emp.reload()
        if employee_id:
            self.emp.set_id(employee_id)
        self.reload()

    def refresh(self):
        self.emp.reload()
        self.reload()

    def reload(self):
        emp_id = self.emp.current_id()
        can_fix = self.can("correct")
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_restore):
            b.setVisible(can_fix)
        if not emp_id:
            self.days.set_rows([])
            return
        start, end = self.period.range()
        rows = S.daily_rows(self.db, start, end, [emp_id], now=self.db.now())
        for r in rows:
            rec = r["_record"]
            r["shift"] = f"{fmt_time(rec.shift.start)}–{fmt_time(rec.shift.end)}" if rec.shift else ""
            r["bar"] = ""
        self.days.set_rows(rows)
        s = E.summarize([r["_record"] for r in rows])
        self.summary.setText(
            f"أيام الدوام: {digits(s.workdays)}   •   حضور: {digits(s.present_days)}   •   غياب: {digits(s.absent_days)}"
            f"   •   إجازات: {digits(s.leave_days)}   •   مرات التأخير: {digits(s.late_count)}"
            f" (مجموع {digits(s.late_minutes)} دقيقة)   •   بصمات ناقصة: {digits(s.missing_count)}")
        corr = S.correction_rows(self.db, start - timedelta(days=60), self.db.now().date(), [emp_id])
        corr = [c for c in corr if any(start <= (t.date() if t else start) <= end + timedelta(days=1)
                                       for t in (c["old_ts"], c["new_ts"])) or (start <= c["created_at"].date() <= end)]
        self.corr.set_rows(corr)
        self._select_day(keep=True)

    def _select_day(self, keep: bool = False):
        r = self.days.selected()
        if r is not None:
            self.selected_date = r["date"]
        elif not keep:
            return
        day = self.selected_date
        if day is None:
            self.day_title.setText("اختر يوماً من الجدول")
            self.punches.set_rows([])
            return
        rec = next((x["_record"] for x in self.days.model_.rows if x["date"] == day), None)
        self.day_title.setText(f"{fmt_date(day)} — {rec.label if rec else ''}")
        emp_id = self.emp.current_id()
        lo, hi = day - timedelta(days=1), day + timedelta(days=1)
        rows = []
        in_day = {p.id for p in rec.punches} if rec else set()
        for p in self.db.punches.list(emp_id, lo, hi, include_deleted=True):
            if p["id"] not in in_day and not (p["deleted"] and p["ts"][:10] == day.isoformat()):
                continue
            state = "محذوفة" if p["deleted"] else ("معدّلة" if p["corrections"] else "")
            rows.append({"id": p["id"], "time": parse_dt(p["ts"]), "source": S.SOURCE_LABELS.get(p["source"]),
                         "kind": S.KIND_LABELS.get(p["kind"]), "score": p["score"], "state": state,
                         "snapshot": p["snapshot"], "deleted": p["deleted"], "ts": p["ts"],
                         "kind_raw": p["kind"],
                         "_tone": "muted" if p["deleted"] else ("warn" if p["corrections"] else "")})
        self.punches.set_rows(rows)
        self.snapshot.clear()

    def _show_snapshot(self):
        r = self.punches.selected()
        self.snapshot.clear()
        if not r or not r.get("snapshot"):
            self.snapshot.setText("لا توجد صورة لهذه البصمة")
            return
        path = snapshots_dir() / r["snapshot"]
        pm = QPixmap(str(path))
        if pm.isNull():
            self.snapshot.setText("الصورة غير متوفرة (ربما حُذفت بعد انتهاء مدة الاحتفاظ)")
        else:
            self.snapshot.setPixmap(pm.scaledToHeight(160, Qt.SmoothTransformation))

    # ----------------------------------------------------------- corrections
    def _punch(self):
        r = self.punches.selected()
        if r is None:
            error(self, "اختر بصمة من قائمة بصمات اليوم.")
        return r

    def add_punch(self):
        emp_id = self.emp.current_id()
        if not emp_id or self.selected_date is None:
            error(self, "اختر الموظف واليوم أولاً.")
            return
        dlg = PunchDialog(self, "إضافة بصمة يدوية", datetime.combine(self.selected_date, datetime.min.time())
                          .replace(hour=8))
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.db.punches.add_manual(emp_id, dlg.when(), dlg.kind.currentData(), dlg.reason())
        except StoreError as exc:
            error(self, str(exc))
            return
        self.toast("أُضيفت البصمة وسُجّل السبب", "ok")
        self.reload()

    def edit_punch(self):
        r = self._punch()
        if not r:
            return
        dlg = PunchDialog(self, "تعديل وقت البصمة", parse_dt(r["ts"]), r["kind_raw"])
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            self.db.punches.edit(r["id"], dlg.when(), dlg.kind.currentData(), dlg.reason())
        except StoreError as exc:
            error(self, str(exc))
            return
        self.toast("تم التعديل وسُجّل السبب", "ok")
        self.reload()

    def delete_punch(self):
        r = self._punch()
        if not r:
            return
        reason = ReasonDialog.ask(self, "حذف بصمة", f"البصمة: {digits(r['ts'][:16])}")
        if reason:
            try:
                self.db.punches.delete(r["id"], reason)
            except StoreError as exc:
                error(self, str(exc))
                return
            self.reload()

    def restore_punch(self):
        r = self._punch()
        if not r:
            return
        reason = ReasonDialog.ask(self, "استرجاع بصمة محذوفة", f"البصمة: {digits(r['ts'][:16])}")
        if reason:
            try:
                self.db.punches.restore(r["id"], reason)
            except StoreError as exc:
                error(self, str(exc))
                return
            self.reload()


class PunchDialog(QDialog):
    def __init__(self, parent, title: str, when: datetime, kind: str = "auto"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.date = DateEdit(when.date())
        self.time = TimeEdit(when.time())
        self.kind = QComboBox()
        for k, t in KIND_CHOICES:
            self.kind.addItem(t, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        form.addRow("التاريخ:", self.date)
        form.addRow("الوقت:", self.time)
        form.addRow("النوع:", self.kind)
        lay.addLayout(form)
        lay.addWidget(label("سبب التعديل (إلزامي):"))
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(80)
        lay.addWidget(self.text)
        quick = QHBoxLayout()
        for q in QUICK_REASONS:
            b = QPushButton(q)
            b.setProperty("kind", "ghost")
            b.clicked.connect(lambda _=False, t=q: self.text.setPlainText(
                (self.text.toPlainText().strip() + " — " if self.text.toPlainText().strip() else "") + t))
            quick.addWidget(b)
        lay.addLayout(quick)
        row = QHBoxLayout()
        ok = QPushButton("حفظ")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self._ok)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        row.addStretch(1)
        lay.addLayout(row)

    def when(self) -> datetime:
        return datetime.combine(self.date.value(), self.time.value())

    def reason(self) -> str:
        return self.text.toPlainText().strip()

    def _ok(self):
        if len(self.reason()) < 3:
            error(self, "اكتب سبب التعديل.")
            return
        self.accept()
