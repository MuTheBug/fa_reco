"""Main window: switches between the kiosk screen and the administration shell."""

from __future__ import annotations

import time
from datetime import timedelta

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMainWindow, QStackedWidget

from .. import APP_SUBTITLE, APP_TITLE
from ..core import maintenance
from ..core.timeutil import parse_dt
from ..face.service import purge_snapshots
from ..kiosk_logic import KioskLogic
from ..notify import telegram
from ..paths import backups_dir, snapshots_dir
from .admin import AdminShell
from .dialogs import LoginDialog
from .face_worker import FaceWorker
from .kiosk import KioskScreen
from .widgets.common import Toast, error


class AppContext:
    """Objects shared by every screen."""

    def __init__(self, db, camera, worker: FaceWorker, window=None, telegram=None):
        self.db = db
        self.camera = camera
        self.worker = worker
        self.window = window
        self.telegram = telegram  # the Telegram sender thread (None when not running)
        self.logic = KioskLogic(db)
        self._settings = db.settings.all()

    def setting(self, key):
        return self._settings.get(key)

    def reload_settings(self):
        self._settings = self.db.settings.all()

    def toast(self, text: str, tone: str = ""):
        if self.window is not None:
            self.window.toaster.show_text(text, tone=tone)


# Without a single frame for this long after the camera had worked, and starting it again did not
# help (a driver stuck since the computer slept), the program restarts itself; at most this often.
RELAUNCH_AFTER_SECONDS = 120
RELAUNCH_EVERY = timedelta(minutes=30)
SLEEP_GAP_SECONDS = 30  # timers stop while the computer sleeps: a longer gap means it woke up
ALERT_TOAST_SECONDS = 30 * 60  # how often the attendance screen repeats a waiting-reports alert


class _ActivityFilter(QObject):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self.callback()
        return False


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, kiosk_first: bool = True):
        super().__init__()
        ctx.window = self
        self.ctx = ctx
        self.setWindowTitle(f"{APP_TITLE} — {APP_SUBTITLE}")
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.kiosk = KioskScreen(ctx)
        self.admin = AdminShell(ctx)
        self.stack.addWidget(self.kiosk)
        self.stack.addWidget(self.admin)
        self.toaster = Toast(self)
        self.kiosk.admin_requested.connect(self.request_admin)
        self.admin.kiosk_requested.connect(self.show_kiosk)
        self.admin.logout_requested.connect(self.show_kiosk)
        QShortcut(QKeySequence("Ctrl+Shift+A"), self, activated=self.request_admin)
        QShortcut(QKeySequence("F11"), self, activated=self._toggle_fullscreen)

        ctx.worker.ready.connect(self._engine_ready)
        ctx.worker.progress.connect(
            lambda i, n: self.kiosk.set_loading(f"جارٍ تجهيز بصمات الوجه… {i}/{n}"))
        self.kiosk.set_loading("جارٍ تحميل نظام التعرّف على الوجه…")

        self._last_activity = time.monotonic()
        self._filter = _ActivityFilter(self._touch)
        from PySide6.QtWidgets import QApplication

        QApplication.instance().installEventFilter(self._filter)
        self._idle = QTimer(self)
        self._idle.timeout.connect(self._check_idle)
        self._idle.start(15_000)

        self.clock_guard = maintenance.ClockGuard(ctx.db, time.monotonic)
        msg = self.clock_guard.startup_check()
        if msg:
            QTimer.singleShot(1500, lambda: self.toaster.show_text("تنبيه أمني: " + msg, 8000, "bad"))
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._housekeeping)
        self._heartbeat.start(60_000)
        QTimer.singleShot(5000, self._housekeeping)
        self._wall = time.time()
        self._camera_timer = QTimer(self)
        self._camera_timer.timeout.connect(self._watch_camera)
        self._camera_timer.start(2000)
        self.relaunch = None  # set by the application: start a new copy of the program and quit
        self._alert_toast_at = -ALERT_TOAST_SECONDS
        self._allow_close = False
        self.in_kiosk = kiosk_first

    # ------------------------------------------------------------- startup
    def start(self):
        if self.in_kiosk:
            self.show_kiosk(initial=True)
        else:
            self.stack.setCurrentWidget(self.admin)
            self.admin.prepare()
            self.showMaximized()

    def _engine_ready(self, err: str):
        if err:
            self.kiosk.set_loading("تعذّر تشغيل نظام التعرّف: " + err)
            return
        self.ctx.logic.engine = self.ctx.worker.engine
        self.kiosk.set_loading("")
        if self.in_kiosk:
            self.ctx.worker.set_mode("recognize")

    # --------------------------------------------------------- navigation
    def show_kiosk(self, initial: bool = False):
        if self.ctx.db.user:
            self.ctx.db.users.logout()
        self.admin.leave()
        self.in_kiosk = True
        self.ctx.reload_settings()
        self.ctx.worker.configure(self.ctx._settings)
        self.kiosk.refresh_settings()
        self.stack.setCurrentWidget(self.kiosk)
        self.kiosk.activate()
        if initial or not self.isFullScreen():
            self.showFullScreen()

    def request_admin(self):
        if not self.in_kiosk:
            return
        dlg = LoginDialog(self.ctx.db, self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.in_kiosk = False
        self.kiosk.deactivate()
        self.ctx.worker.set_mode("idle")
        self.stack.setCurrentWidget(self.admin)
        self.admin.prepare()
        self._touch()
        self.showMaximized()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    # ------------------------------------------------------------ idle/admin
    def _touch(self):
        self._last_activity = time.monotonic()

    def _check_idle(self):
        if self.in_kiosk:
            return
        limit = float(self.ctx.setting("kiosk.admin_timeout_minutes") or 0) * 60
        if limit and time.monotonic() - self._last_activity > limit and not self._modal_open():
            self.show_kiosk()
            self.toaster.show_text("انتهت جلسة الإدارة لعدم النشاط", tone="warn")

    def _modal_open(self) -> bool:
        from PySide6.QtWidgets import QApplication

        return QApplication.activeModalWidget() is not None

    # --------------------------------------------------------------- camera
    def _watch_camera(self) -> str:
        """Keep the camera alive: after the laptop slept (its lid closed) the camera handle is
        usually dead, so it is opened again; so is a camera whose frames stopped coming."""
        now = time.time()
        slept, self._wall = now - self._wall > SLEEP_GAP_SECONDS, now
        cam = self.ctx.camera
        if slept and cam.wanted:
            cam.restart("the computer woke up")
            return "resumed"
        why = cam.watch()
        if not why and self._should_relaunch():
            self._relaunch_now()
            return "relaunched"
        return why

    def _should_relaunch(self) -> bool:
        if self.relaunch is None or not self.in_kiosk or self._modal_open():
            return False
        if self.ctx.camera.dead_for() < RELAUNCH_AFTER_SECONDS:
            return False
        db = self.ctx.db
        last = db.settings.get("camera.last_relaunch") or ""
        return not last or db.now() - parse_dt(last) >= RELAUNCH_EVERY

    def _relaunch_now(self):
        db = self.ctx.db
        db.settings.set("camera.last_relaunch", db.now_s(), audit=False)
        db.events.add("camera", "توقفت الكاميرا ولم تعد إلى العمل بعد إعادة فتحها؛ أُعيد تشغيل البرنامج تلقائياً")
        self._allow_close = True
        self._shutdown()
        self.relaunch()

    # ---------------------------------------------------------- maintenance
    def _housekeeping(self):
        db = self.ctx.db
        msg = self.clock_guard.heartbeat()
        if msg:
            self.toaster.show_text("تنبيه أمني: " + msg, 8000, "bad")
        try:
            if maintenance.auto_backup_due(db):
                folder = backups_dir(db.settings.get("backup.dir") or "")
                maintenance.backup(db, folder, bool(db.settings.get("backup.include_snapshots")), "auto")
                maintenance.prune_backups(folder, int(db.settings.get("backup.keep") or 30))
        except Exception as exc:  # e.g. USB backup folder not plugged in
            db.events.add("backup", f"تعذّر إنشاء النسخة الاحتياطية التلقائية: {exc}")
        today = db.now().date().isoformat()
        if db.settings.get("maintenance.last_purge") != today:
            purge_snapshots(snapshots_dir(), int(db.settings.get("kiosk.snapshot_days") or 0), db.now())
            db.settings.set("maintenance.last_purge", today, audit=False)
        try:
            at = telegram.report_due(db, db.now())
            if at is not None:
                db.settings.set("telegram.last_list", f"{today} {at:%H:%M}", audit=False)
                if telegram.queue_report(db, db.now().date(), at) and self.ctx.telegram:
                    self.ctx.telegram.wake()
        except Exception as exc:  # the attendance screen must keep working whatever happens here
            db.events.add("telegram", f"تعذّر إعداد تقرير الحضور: {exc}")
        self.show_alerts()

    def show_alerts(self) -> str:
        """Reports held up (no internet) are shown on both screens, and every half hour the
        attendance screen also says so in large letters."""
        try:
            text = telegram.waiting_alert(self.ctx.db, self.ctx.db.now())
        except Exception:
            text = ""
        self.kiosk.alert.set_text(text)
        self.admin.alert.set_text(text)
        if text and self.in_kiosk and time.monotonic() - self._alert_toast_at >= ALERT_TOAST_SECONDS:
            self._alert_toast_at = time.monotonic()
            self.toaster.show_text(text, 20_000, "warn")
        return text

    # ---------------------------------------------------------------- close
    def closeEvent(self, ev):
        if self._allow_close or not self.in_kiosk:
            self._shutdown()
            ev.accept()
            return
        dlg = LoginDialog(self.ctx.db, self)
        dlg.setWindowTitle("إغلاق البرنامج — يتطلب كلمة مرور الإدارة")
        if dlg.exec() == QDialog.Accepted and dlg.user and dlg.user.role == "admin":
            self.ctx.db.users.logout()
            self._shutdown()
            ev.accept()
        else:
            if dlg.user:
                self.ctx.db.users.logout()
                error(self, "إغلاق البرنامج متاح لمدير النظام فقط.")
            ev.ignore()

    def _shutdown(self):
        self.ctx.worker.stop()
        self.ctx.camera.stop()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.toaster.isVisible():
            self.toaster.move((self.width() - self.toaster.width()) // 2,
                              self.height() - self.toaster.height() - 28)

