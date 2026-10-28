"""Program entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import APP_TITLE, __version__


def _setup_logging() -> None:
    from .paths import logs_dir

    handler = RotatingFileHandler(logs_dir() / "hadir.log", maxBytes=2_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _excepthook(exc_type, exc, tb):
    logging.getLogger("hadir").error("unhandled error\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    try:
        from PySide6.QtWidgets import QApplication

        from .ui.widgets.common import error

        if QApplication.instance():
            error(None, f"حدث خطأ غير متوقع وتم تسجيله في ملف السجل:\n{exc}")
    except Exception:
        pass


def _qt_app(argv):
    from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
    from PySide6.QtWidgets import QApplication

    from .fonts import register_fonts
    from .ui.theme import apply_theme

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_TITLE)
    app.setApplicationVersion(__version__)
    app.setLayoutDirection(Qt.RightToLeft)
    QLocale.setDefault(QLocale(QLocale.Arabic, QLocale.Iraq))
    app._translators = []
    for name in ("qtbase_ar", "qt_ar"):
        tr = QTranslator(app)
        if tr.load(name, QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(tr)
            app._translators.append(tr)
    register_fonts()
    apply_theme(app)
    return app


def run_gui(args) -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QDialog

    from .core.timeutil import Fmt
    from .db.store import Database
    from .face.camera import CameraSource
    from .paths import data_dir, db_path
    from .ui.dialogs import FirstRunDialog
    from .ui.face_worker import FaceWorker
    from .ui.main_window import AppContext, MainWindow
    from .ui.theme import apply_theme
    from .ui.widgets.common import error

    app = _qt_app(sys.argv)
    lock = QLockFile(str(data_dir() / "hadir.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        error(None, "البرنامج يعمل بالفعل على هذا الجهاز.")
        return 1

    if args.demo:
        from .demo import demo_database

        db_file = data_dir() / "demo.db"
        fresh = not db_file.exists()
        db = Database(db_file)
        if fresh:
            db.close()
            demo_database(db_file).close()
            db = Database(db_file)
    else:
        db = Database(db_path())
    if not db.users.count():
        if FirstRunDialog(db).exec() != QDialog.Accepted:
            return 0
        db.users.logout()
    s = db.settings.all()
    Fmt.configure(bool(s["ui.arabic_digits"]), s["ui.month_style"])
    apply_theme(app, float(s.get("ui.font_scale") or 1.0))
    camera = CameraSource(int(s["camera.index"]), s["camera.backend"], int(s["camera.width"]),
                          int(s["camera.height"]), args.camera_source or s["camera.source"])
    camera.start()
    worker = FaceWorker(db, camera, s)
    worker.start()
    ctx = AppContext(db, camera, worker)
    win = MainWindow(ctx, kiosk_first=bool(s["kiosk.start_in_kiosk"]) and not args.admin)
    win.start()
    if args.admin:
        win.in_kiosk = True
        win.request_admin()
    code = app.exec()
    lock.unlock()
    return code


def selftest(args) -> int:
    """Headless check used by the Windows build: models, database, reports."""
    import tempfile
    from datetime import date

    import cv2
    import numpy as np

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    out = []
    from .face import models

    for spec in models.ALL_MODELS:
        path = models.model_path(spec)
        ok = path.is_file() and (not spec.sha256 or models.sha256_of(path) == spec.sha256)
        out.append(f"model {spec.id}: {'ok' if ok else 'MISSING/BAD'}")
        if not ok:
            print("\n".join(out))
            return 2
    from .face.pipeline import FaceEngine

    engine = FaceEngine("dual")
    blank = np.full((720, 1280, 3), 128, np.uint8)
    a = engine.analyze(blank)
    out.append(f"engine ok, faces on blank frame: {len(a.faces)}")
    if args.selftest_image:
        img = cv2.imread(args.selftest_image)
        a = engine.analyze(img, embed=True)
        if a.face is None:
            print("\n".join(out + ["no face detected in self-test image"]))
            return 3
        emb = engine.embed_all(a.chip)
        out.append(f"face detected; embeddings: {', '.join(f'{k}:{v.shape[0]}' for k, v in emb.items())};"
                   f" liveness {engine.liveness.score(img, a.face):.3f}")
    app = _qt_app(["hadir"])
    from .demo import demo_database
    from .reports import builtin
    from .reports.excel import export_xlsx
    from .reports.render import export_pdf

    with tempfile.TemporaryDirectory() as tmp:
        db = demo_database(Path(tmp) / "t.db", date(2026, 10, 28), employees=10, days=20)
        for rid, *_ in builtin.CATALOG:
            rep = builtin.build(db, rid, builtin.default_params(rid, date(2026, 10, 28)))
            export_xlsx(rep, str(Path(tmp) / f"{rid}.xlsx"))
            pages = export_pdf(rep, str(Path(tmp) / f"{rid}.pdf"))
            out.append(f"report {rid}: {pages} page(s)")
        db.close()
    del app
    print("\n".join(out))
    print("SELFTEST OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hadir", description=f"{APP_TITLE} {__version__}")
    ap.add_argument("--data-dir", help="مجلد البيانات (بدلاً من المجلد الافتراضي)")
    ap.add_argument("--demo", action="store_true", help="تشغيل بقاعدة بيانات تجريبية")
    ap.add_argument("--admin", action="store_true", help="فتح شاشة الإدارة مباشرة")
    ap.add_argument("--camera-source", default="", help="رقم كاميرا أو ملف فيديو أو مجلد صور")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--selftest-image", default="", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.data_dir:
        os.environ["HADIR_DATA_DIR"] = args.data_dir
    if args.selftest:
        return selftest(args)
    _setup_logging()
    sys.excepthook = _excepthook
    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
