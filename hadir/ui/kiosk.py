"""The attendance screen shown at the department door."""

from __future__ import annotations

import base64
from datetime import datetime

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from ..core.settings import SECURITY_LEVELS
from ..face.pipeline import Status
from ..core.timeutil import digits, fmt_date_long, fmt_time, parse_dt
from ..kiosk_logic import KioskResult
from .sound import play
from .theme import C, FONT_FAMILY, icon, pixmap
from .widgets.camera_view import CameraView
from .widgets.common import error

TONE_COLORS = {"ok": "#16a34a", "warn": "#d97706", "bad": "#dc2626"}


def round_pixmap(data: bytes | None, size: int) -> QPixmap | None:
    if not data:
        return None
    img = QImage.fromData(data)
    if img.isNull():
        return None
    img = img.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(path)
    p.drawImage(QRectF(0, 0, size, size), img,
                QRectF((img.width() - size) / 2, (img.height() - size) / 2, size, size))
    p.end()
    return out


def logo_pixmap(b64: str, height: int) -> QPixmap | None:
    if not b64:
        return None
    img = QImage.fromData(base64.b64decode(b64))
    if img.isNull():
        return None
    return QPixmap.fromImage(img.scaledToHeight(height, Qt.SmoothTransformation))


class ResultCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ResultCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(30, 20, 30, 20)
        lay.setSpacing(6)
        self.icon = QLabel(alignment=Qt.AlignCenter)
        self.photo = QLabel(alignment=Qt.AlignCenter)
        self.name = QLabel(alignment=Qt.AlignCenter)
        self.name.setWordWrap(True)
        self.code = QLabel(alignment=Qt.AlignCenter)
        self.title = QLabel(alignment=Qt.AlignCenter)
        self.time = QLabel(alignment=Qt.AlignCenter)
        self.lines = QLabel(alignment=Qt.AlignCenter)
        self.lines.setWordWrap(True)
        for w in (self.icon, self.photo, self.name, self.code, self.title, self.time, self.lines):
            lay.addWidget(w, 0, Qt.AlignHCenter)
        self.name.setStyleSheet("font-size: 26pt; font-weight: bold; color: #0f2c4c;")
        self.code.setStyleSheet("font-size: 12pt; color: #64748b;")
        self.time.setStyleSheet("font-size: 30pt; font-weight: bold; color: #0f2c4c;")
        self.lines.setStyleSheet("font-size: 15pt; color: #334155;")
        self.hide()

    def show_result(self, res: KioskResult, photo: bytes | None):
        color = TONE_COLORS.get(res.tone, "#0f2c4c")
        self.setStyleSheet(f"#ResultCard {{ background: white; border-radius: 22px;"
                           f" border: 4px solid {color}; }}")
        name = {"ok": "check", "warn": "alert", "bad": "x"}.get(res.tone, "check")
        self.icon.setPixmap(pixmap(name, color, 64))
        pm = round_pixmap(photo, 128)
        self.photo.setVisible(pm is not None)
        self.icon.setVisible(pm is None)
        if pm is not None:
            self.photo.setPixmap(pm)
            self.photo.setFixedSize(136, 136)
            self.photo.setStyleSheet(f"border: 4px solid {color}; border-radius: 68px;")
        self.name.setText(res.name)
        self.name.setVisible(bool(res.name))
        self.code.setText(f"الرقم الوظيفي {digits(res.code)}" if res.code else "")
        self.code.setVisible(bool(res.code))
        self.title.setText(res.title)
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"font-size: 22pt; font-weight: bold; color: {color};")
        area = self.parentWidget()
        if area is not None:
            self.setMaximumWidth(int(area.width() * 0.92))
            self.setMaximumHeight(int(area.height() * 0.96))
        self.time.setText(fmt_time(res.ts) if res.ts and res.kind == "punched" else "")
        self.time.setVisible(res.kind == "punched")
        self.lines.setText("\n".join(res.lines))
        self.adjustSize()
        self.show()
        self.raise_()


class KioskScreen(QWidget):
    admin_requested = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("Kiosk")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header
        head = QFrame()
        head.setObjectName("KioskHeader")
        head.setStyleSheet(f"#KioskHeader {{ background: {C['navy']}; }} QLabel {{ color: white; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(28, 14, 28, 14)
        self.logo = QLabel()
        hl.addWidget(self.logo)
        org_box = QVBoxLayout()
        self.org = QLabel()
        self.org.setStyleSheet("font-size: 22pt; font-weight: bold;")
        self.sub = QLabel()
        self.sub.setStyleSheet("font-size: 12pt; color: #b7c7d9;")
        org_box.addWidget(self.org)
        org_box.addWidget(self.sub)
        hl.addLayout(org_box, 1)
        clock_box = QVBoxLayout()
        self.clock = QLabel(alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.clock.setStyleSheet("font-size: 34pt; font-weight: bold;")
        self.date = QLabel(alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.date.setStyleSheet("font-size: 13pt; color: #dbe5f1;")
        clock_box.addWidget(self.clock)
        clock_box.addWidget(self.date)
        hl.addLayout(clock_box)
        root.addWidget(head)

        # body
        body = QWidget()
        body.setObjectName("KioskBody")
        bl = QHBoxLayout(body)
        bl.setContentsMargins(24, 20, 24, 12)
        bl.setSpacing(20)
        cam_wrap = QWidget()
        cg = QGridLayout(cam_wrap)
        cg.setContentsMargins(0, 0, 0, 0)
        self.camera = CameraView(mirror=bool(ctx.setting("camera.mirror")))
        cg.addWidget(self.camera, 0, 0)
        self.result = ResultCard()
        cg.addWidget(self.result, 0, 0, Qt.AlignCenter)
        bl.addWidget(cam_wrap, 3)

        side = QFrame()
        side.setObjectName("Card")
        side.setMinimumWidth(300)
        side.setMaximumWidth(420)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 16, 16, 16)
        self.present_lbl = QLabel()
        self.present_lbl.setStyleSheet(f"font-size: 13pt; color: {C['navy']}; font-weight: bold;")
        sl.addWidget(self.present_lbl)
        title = QLabel("آخر التسجيلات")
        title.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {C['navy']};")
        sl.addWidget(title)
        self.recent = QListWidget()
        self.recent.setFocusPolicy(Qt.NoFocus)
        self.recent.setWordWrap(True)
        self.recent.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent.setStyleSheet("QListWidget { border: none; font-size: 12.5pt; }"
                                  " QListWidget::item { padding: 9px 4px; border-bottom: 1px solid #edf0f4; }")
        sl.addWidget(self.recent, 1)
        self.side = side
        bl.addWidget(side, 1)
        root.addWidget(body, 1)

        # footer
        foot = QFrame()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 6, 24, 14)
        self.tip = QLabel("قف أمام الكاميرا وانظر إليها مباشرة — يتم التسجيل تلقائياً")
        self.tip.setStyleSheet(f"color: {C['muted']}; font-size: 12pt;")
        fl.addWidget(self.tip, 1)
        self.pin_btn = QPushButton("  التسجيل بالرقم الوظيفي")
        self.pin_btn.setIcon(icon("lock", C["primary"], 18))
        self.pin_btn.clicked.connect(self._pin)
        fl.addWidget(self.pin_btn)
        self.admin_btn = QPushButton()
        self.admin_btn.setIcon(icon("settings", C["muted"], 20))
        self.admin_btn.setToolTip("الإدارة (Ctrl+Shift+A)")
        self.admin_btn.setFixedSize(QSize(44, 40))
        self.admin_btn.clicked.connect(self.admin_requested.emit)
        fl.addWidget(self.admin_btn)
        root.addWidget(foot)

        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._pull_frame)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.result.hide)
        self._last_seq = -1
        self._loading = ""
        ctx.worker.status.connect(self._on_status)
        ctx.worker.decision.connect(self._on_decision)
        self.refresh_settings()

    # --------------------------------------------------------------- state
    def refresh_settings(self):
        ctx = self.ctx
        self.org.setText(ctx.setting("org.name"))
        self.sub.setText(ctx.setting("org.subtitle") or "تسجيل الحضور والانصراف ببصمة الوجه")
        pm = logo_pixmap(ctx.setting("org.logo"), 64)
        self.logo.setVisible(pm is not None)
        if pm is not None:
            self.logo.setPixmap(pm)
        self.camera.mirror = bool(ctx.setting("camera.mirror"))
        self.pin_btn.setVisible(bool(ctx.setting("kiosk.allow_pin")))
        self.side.setVisible(bool(ctx.setting("kiosk.show_recent")))
        self._tick()
        self.reload_recent()

    def activate(self):
        self._frame_timer.start(33)
        self._clock_timer.start(1000)
        self._tick()
        self.reload_recent()
        if self.ctx.worker.ready_ok:
            self.ctx.worker.set_mode("recognize")

    def deactivate(self):
        self._frame_timer.stop()
        self._clock_timer.stop()
        self.result.hide()

    def set_loading(self, text: str):
        """Show why recognition is not running yet (loading models, or an error)."""
        self._loading = text
        self.camera.placeholder = text or "جارٍ تشغيل الكاميرا…"
        self.camera.set_status(Status(state="idle", message=text, tone="warn") if text else None)

    # --------------------------------------------------------------- timers
    def _tick(self):
        now = self.ctx.db.now()
        self.clock.setText(digits(now.strftime("%H:%M:%S")))
        self.date.setText(fmt_date_long(now.date()))

    def _pull_frame(self):
        cam = self.ctx.camera
        frame, seq = cam.latest()
        if cam.state == "error" and frame is None:
            self.camera.placeholder = cam.error
            self.camera.set_frame(None)
            return
        if seq != self._last_seq and frame is not None:
            self._last_seq = seq
            fill = bool(self.ctx.setting("kiosk.fill_light")) and 0 < cam.brightness < 75
            if fill != self.camera.fill_light:
                self.camera.fill_light = fill
            self.camera.set_frame(frame)

    # ------------------------------------------------------------ recognition
    def _on_status(self, st):
        if self._loading:
            return
        self.camera.set_status(st)

    def _on_decision(self, decision, frame):
        thr = SECURITY_LEVELS.get(self.ctx.setting("recog.security"), SECURITY_LEVELS["high"])["arc"]
        try:
            res = self.ctx.logic.handle(decision, frame, self.ctx.db.now(), thr)
        except Exception as exc:  # never let the kiosk crash on a database problem
            res = KioskResult("error", "bad", "حدث خطأ أثناء التسجيل", [str(exc)])
        self.show_result(res)
        if res.adapted:
            self.ctx.worker.reload_galleries()

    def show_result(self, res: KioskResult):
        photo = self.ctx.db.employees.photo(res.employee_id) if res.employee_id else None
        self.result.show_result(res, photo)
        if self.ctx.setting("kiosk.sound"):
            play(res.tone)
        self._hide_timer.start(int(float(self.ctx.setting("kiosk.result_seconds") or 3) * 1000))
        if res.kind == "punched":
            self._add_recent(res.ts, res.name, res.punch_type, front=True)
            self._update_present()

    def reload_recent(self):
        self.recent.clear()
        db = self.ctx.db
        today = db.now().date()
        rows = [r for r in db.punches.recent(12) if r["ts"][:10] == today.isoformat()][:10]
        for r in reversed(rows):
            self._add_recent(parse_dt(r["ts"]), r["full_name"], "", front=True)
        self._update_present()

    def _add_recent(self, ts: datetime, name: str, kind: str, front: bool):
        if not self.ctx.setting("kiosk.show_names_recent"):
            name = "موظف"
        label = {"in": "حضور", "out": "انصراف"}.get(kind, "")
        text = f"{name}\n{fmt_time(ts)}" + (f" — {label}" if label else "")
        item = QListWidgetItem(text)
        item.setIcon(icon("check", C["ok"], 16))
        if front:
            self.recent.insertItem(0, item)
        while self.recent.count() > 10:
            self.recent.takeItem(self.recent.count() - 1)

    def _update_present(self):
        db = self.ctx.db
        today = db.now().date().isoformat()
        n = db.scalar("SELECT COUNT(DISTINCT employee_id) FROM punches WHERE deleted = 0 AND ts >= ?"
                      " AND ts < date(?, '+1 day')", (today, today))
        self.present_lbl.setText(f"سجّلوا اليوم: {digits(n)} موظف")

    # ------------------------------------------------------------------ PIN
    def _pin(self):
        dlg = PinDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        row = self.ctx.db.employees.check_pin(dlg.code.text(), dlg.pin.text())
        if row is None:
            self.ctx.db.events.add("login_failed", f"رمز سري خاطئ للرقم الوظيفي {dlg.code.text()}")
            self.show_result(KioskResult("unknown", "bad", "الرقم الوظيفي أو الرمز غير صحيح"))
            return
        frame, _ = self.ctx.camera.latest()
        h, w = (frame.shape[:2] if frame is not None else (0, 0))
        res = self.ctx.logic.register(row["id"], self.ctx.db.now(), source="pin", frame=frame,
                                      box=(w * 0.25, h * 0.1, w * 0.5, h * 0.8) if frame is not None else None)
        if res.kind == "punched":
            res.lines.append("سُجّلت بالرمز السري وتظهر في التقارير للمراجعة")
        self.show_result(res)


class PinDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("التسجيل بالرقم الوظيفي")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("الرقم الوظيفي:"))
        self.code = QLineEdit()
        lay.addWidget(self.code)
        lay.addWidget(QLabel("الرمز السري:"))
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.Password)
        lay.addWidget(self.pin)
        row = QHBoxLayout()
        ok = QPushButton("تسجيل")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self._ok)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        f = QFont(FONT_FAMILY)
        f.setPointSize(14)
        self.setFont(f)

    def _ok(self):
        if not self.code.text().strip() or not self.pin.text():
            error(self, "أدخل الرقم الوظيفي والرمز السري.")
            return
        self.accept()

