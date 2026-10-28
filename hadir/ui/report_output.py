"""Preview / print / PDF / Excel for any Report."""

from __future__ import annotations

import base64
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtPrintSupport import QPrintPreviewDialog, QPrinter
from PySide6.QtWidgets import QFileDialog

from ..reports.excel import export_xlsx
from ..reports.model import Report
from ..reports.render import export_pdf, page_layout, paint_on
from .widgets.common import error


def org_logo(db) -> QImage | None:
    b64 = db.settings.get("org.logo")
    if not b64:
        return None
    img = QImage.fromData(base64.b64decode(b64))
    return None if img.isNull() else img


def _safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", title).strip() or "تقرير"


def preview(parent, db, report: Report) -> None:
    printer = QPrinter(QPrinter.HighResolution)
    printer.setPageLayout(page_layout(report))
    printer.setDocName(report.title)
    dlg = QPrintPreviewDialog(printer, parent)
    dlg.setWindowTitle(f"معاينة الطباعة — {report.title}")
    dlg.setLayoutDirection(Qt.RightToLeft)
    logo = org_logo(db)
    dlg.paintRequested.connect(lambda p: paint_on(p, report, logo))
    dlg.resize(1100, 820)
    dlg.exec()
    db.audit("طباعة/معاينة تقرير", "report", None, report.title)


def save_pdf(parent, db, report: Report) -> None:
    path, _ = QFileDialog.getSaveFileName(parent, "حفظ PDF", _safe_name(report.title) + ".pdf", "PDF (*.pdf)")
    if not path:
        return
    try:
        export_pdf(report, path, org_logo(db))
    except OSError as exc:
        error(parent, f"تعذّر الحفظ: {exc}")
        return
    db.audit("تصدير تقرير PDF", "report", None, report.title)
    if hasattr(parent.window(), "toaster"):
        parent.window().toaster.show_text("تم حفظ ملف PDF", tone="ok")


def save_excel(parent, db, report: Report) -> None:
    path, _ = QFileDialog.getSaveFileName(parent, "تصدير إلى Excel", _safe_name(report.title) + ".xlsx",
                                          "Excel (*.xlsx)")
    if not path:
        return
    try:
        export_xlsx(report, path)
    except OSError as exc:
        error(parent, f"تعذّر الحفظ (هل الملف مفتوح في Excel؟): {exc}")
        return
    db.audit("تصدير تقرير Excel", "report", None, report.title)
    if hasattr(parent.window(), "toaster"):
        parent.window().toaster.show_text("تم التصدير إلى Excel", tone="ok")
