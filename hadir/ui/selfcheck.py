"""Open every administration page without a camera or recognition models.

The pages are imported by name the first time they are opened (see
``AdminShell.page``), so a build that leaves one out fails only when someone
clicks it.  The packaged program's ``--selftest`` calls ``open_every_page`` to
prove that each page is inside the build and works there; the UI tests use the
same stand-ins.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication


class NoCamera:
    """Camera stand-in that never delivers a frame."""

    state, error, fps, brightness = "error", "لا توجد كاميرا", 0.0, 0.0

    def latest(self):
        return None, 0

    def start(self):
        pass

    def stop(self):
        pass


class IdleFaceWorker(QObject):
    """FaceWorker stand-in: the same signals, no recognition engine."""

    ready = Signal(str)
    progress = Signal(int, int)
    status = Signal(object)
    decision = Signal(object, object)
    enroll_status = Signal(object)
    analysis = Signal(object, object)

    engine = None
    ready_ok = False
    load_error = ""
    mode = "idle"

    def set_mode(self, mode):
        self.mode = mode

    def configure(self, settings):
        pass

    def reload_galleries(self):
        pass

    def pause(self, timeout=0):
        return self.mode

    def stop(self):
        pass


def open_every_page(db) -> int:
    """Build the main window on ``db``, then open and refresh every admin page.
    Returns the number of pages; any failure raises."""
    from .admin import PAGES
    from .main_window import AppContext, MainWindow

    win = MainWindow(AppContext(db, NoCamera(), IdleFaceWorker()), kiosk_first=False)
    try:
        win.start()
        for key, *_ in PAGES:
            win.admin.show_page(key)
            QApplication.processEvents()
            win.admin.pages[key].refresh()
    finally:
        win._allow_close = True
        win.close()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return len(PAGES)
