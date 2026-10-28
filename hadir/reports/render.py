"""Paginated A4 rendering of reports with QPainter (print, preview and PDF).

Tables are laid out right-to-left (first column on the right), the column
header is repeated on every page, and each page carries the organisation
header, page numbers ("صفحة ٢ من ٥") and, on the last page, signature boxes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize,
                           QPainter, QPdfWriter, QPen)

from .. import APP_TITLE
from ..core.timeutil import digits
from .model import Report, format_value

FONT = "Noto Sans Arabic"
MM = 1 / 25.4  # inch per mm

TONE_BG = {"bad": "#fde2e2", "warn": "#fff1d6", "info": "#e3eefc", "ok": "#e3f5e8",
           "muted": "#f1f3f5"}
TONE_FG = {"muted": "#6b7280"}
HEAD_BG = QColor("#dde6f0")
SECTION_BG = QColor("#e9eef5")
SUBTOTAL_BG = QColor("#f2f4f7")
GRID = QColor("#8c97a4")


@dataclass
class _Row:
    kind: str  # data | section | subtotal | total
    cells: list
    height: float
    tone: str = ""
    tones: dict = field(default_factory=dict)


@dataclass
class _Page:
    rows: list = field(default_factory=list)
    notes: bool = False
    signatures: bool = False


class ReportRenderer:
    def __init__(self, report: Report, logo: QImage | None = None, font_family: str = FONT):
        self.r = report
        self.logo = logo if logo is not None and not logo.isNull() else None
        self.family = font_family
        self.pages: list[_Page] = []

    # ---------------------------------------------------------------- fonts
    def font(self, pt: float, bold: bool = False) -> QFont:
        # Pixel-sized fonts keep measuring and painting identical on any device resolution.
        f = QFont(self.family)
        f.setPixelSize(max(1, round(pt * self.r.font_scale * self.dpi / 72)))
        f.setBold(bold)
        return f

    def _px(self, mm: float) -> float:
        return mm * MM * self.dpi

    # --------------------------------------------------------------- layout
    def layout(self, page_rect: QRectF, dpi: float) -> int:
        """Split the report into pages for a printable area (device pixels)."""
        self.dpi = dpi
        self.page = page_rect
        self.pad = self._px(0.8)
        self.body_font = self.font(8.5)
        self.head_font = self.font(8.5, True)
        self.fm = QFontMetricsF(self.body_font)
        self.fm_bold = QFontMetricsF(self.head_font)
        # Arabic fonts reserve a tall line box for stacked marks; single-line rows use a
        # tighter box (text is drawn unclipped so nothing is cut).
        self.single_h = self.body_font.pixelSize() * 1.5 + 2 * self.pad

        rows = self._build_rows()
        self.col_w = self._auto_widths(rows)
        for row in rows:
            row.height = self._row_height(row)
        self.header_h = self._header_height(first=True)
        self.cont_header_h = self._header_height(first=False)
        self.footer_h = self._px(9)
        self.colhead = self._measure([c.title for c in self.r.columns], self.col_w, bold=True)
        notes_h = self._notes_height()
        sig_h = self._px(24) if self.r.signatures else 0.0

        pages = [_Page()]
        avail = page_rect.height() - self.header_h - self.footer_h - self.colhead
        used = 0.0
        for row in rows:
            fresh = (row.kind == "section" and self.r.page_per_section and pages[-1].rows)
            if (fresh or used + row.height > avail) and pages[-1].rows:
                pages.append(_Page())
                avail = page_rect.height() - self.cont_header_h - self.footer_h - self.colhead
                used = 0.0
            pages[-1].rows.append(row)
            used += row.height
        tail = notes_h + sig_h
        if tail:
            if used + tail + self._px(4) > avail:
                pages.append(_Page())
            pages[-1].notes = True
            pages[-1].signatures = bool(self.r.signatures)
        self.pages = pages
        return len(pages)

    def _auto_widths(self, rows) -> list[float]:
        """Column widths from the content, then the spare room shared by the weights."""
        cols = self.r.columns
        total = self.page.width()
        pad2 = 2 * self.pad + self._px(0.6)
        need, floor = [], []
        for i, c in enumerate(cols):
            words = [w for line in c.title.split("\n") for w in line.split()] or [""]
            head_min = max(self.fm_bold.horizontalAdvance(w) for w in words)
            head_line = max(self.fm_bold.horizontalAdvance(line) for line in c.title.split("\n"))
            texts = [row.cells[i] for row in rows if row.kind in ("data", "subtotal", "total")
                     and i < len(row.cells) and row.cells[i]]
            content = max((self.fm.horizontalAdvance(t) for t in texts), default=0.0)
            cap = total * (0.34 if not c.numeric else 0.16)
            need.append(min(max(content, head_min, min(head_line, total * 0.12)), cap) + pad2)
            floor.append(head_min + pad2)
        spare = total - sum(need)
        weights = [max(c.width, 0.1) for c in cols]
        if spare >= 0:
            wsum = sum(weights)
            return [n + spare * w / wsum for n, w in zip(need, weights)]
        # Too wide: lower a common ceiling so only the widest columns wrap (water-filling).
        lo, hi = 0.0, max(need)
        for _ in range(40):
            level = (lo + hi) / 2
            width = sum(max(min(n, level), f) for n, f in zip(need, floor))
            lo, hi = (level, hi) if width < total else (lo, level)
        widths = [max(min(n, lo), f) for n, f in zip(need, floor)]
        scale = total / sum(widths)
        return [w * scale for w in widths]

    def _measure(self, texts, widths, bold=False) -> float:
        fm = self.fm_bold if bold else self.fm
        best = 0.0
        for text, w in zip(texts, widths):
            if not text:
                continue
            rect = fm.boundingRect(QRectF(0, 0, max(w - 2 * self.pad, 4), 10_000),
                                   int(Qt.TextWordWrap | Qt.AlignRight), str(text))
            best = max(best, rect.height())
        if best <= fm.height() * 1.05:
            return self.single_h
        return best + 2 * self.pad

    def _row_height(self, row: _Row) -> float:
        if row.kind == "section":
            return self._measure(row.cells, [self.page.width()], bold=True)
        return self._measure(row.cells, self.col_w, bold=row.kind != "data")

    def _header_height(self, first: bool) -> float:
        if not first:
            return self._px(12)
        h = self._px(30)
        if self.r.subtitle:
            h += self._px(2)
        return h

    def _row(self, values: dict, kind="data") -> _Row:
        cells = [format_value(values.get(c.key), c.kind) for c in self.r.columns]
        return _Row(kind, cells, 0.0, values.get("_tone", ""), values.get("_tones", {}) or {})

    def _build_rows(self) -> list[_Row]:
        out: list[_Row] = []
        if self.r.sections:
            for sec in self.r.sections:
                out.append(_Row("section", [sec.title], 0.0))
                out += [self._row(v) for v in sec.rows]
                if sec.subtotal and any(v not in (None, "", 0) for v in sec.subtotal.values()):
                    sub = dict(sec.subtotal)
                    out.append(self._labelled(sub, "المجموع", "subtotal"))
        else:
            out += [self._row(v) for v in self.r.rows]
        if not out:
            out.append(_Row("section", ["لا توجد بيانات ضمن الشروط المحددة"], 0.0))
        if self.r.totals and any(v not in (None, "", 0) for v in self.r.totals.values()):
            out.append(self._labelled(dict(self.r.totals), "المجموع الكلي", "total"))
        return out

    def _labelled(self, values: dict, label: str, kind: str) -> _Row:
        row = self._row(values, kind)
        for i, c in enumerate(self.r.columns):
            if not c.numeric and not row.cells[i]:
                row.cells[i] = label
                break
        row.kind = kind
        return row

    def _notes_height(self) -> float:
        if not self.r.notes:
            return 0.0
        h = self._px(3)
        for n in self.r.notes:
            rect = self.fm.boundingRect(QRectF(0, 0, self.page.width(), 10_000),
                                        int(Qt.TextWordWrap | Qt.AlignRight), n)
            h += rect.height() + self._px(1)
        return h

    # ---------------------------------------------------------------- paint
    def paint(self, painter: QPainter, index: int) -> None:
        painter.save()
        painter.setLayoutDirection(Qt.RightToLeft)
        page = self.pages[index]
        y = self.page.top()
        y = self._paint_header(painter, y, first=index == 0)
        y = self._paint_table_header(painter, y)
        for row in page.rows:
            self._paint_row(painter, row, y)
            y += row.height
        if page.notes:
            y = self._paint_notes(painter, y + self._px(3))
        if page.signatures:
            self._paint_signatures(painter, y + self._px(4))
        self._paint_footer(painter, index)
        painter.restore()

    def _text(self, painter, rect: QRectF, text: str, align, font: QFont, color="#111827"):
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, int(align | Qt.TextWordWrap | Qt.TextDontClip), text)

    def _paint_header(self, p: QPainter, y: float, first: bool) -> float:
        r = self.page
        meta = self.r.meta
        org = meta.get("org", "") or ""
        if not first:
            h = self.cont_header_h
            self._text(p, QRectF(r.left(), y, r.width(), h - self._px(3)),
                       f"{self.r.title} — {org}", Qt.AlignRight | Qt.AlignVCenter, self.font(9, True),
                       "#374151")
            p.setPen(QPen(GRID, self._px(0.3)))
            p.drawLine(QPointF(r.left(), y + h - self._px(2)), QPointF(r.right(), y + h - self._px(2)))
            return y + h
        h = self.header_h
        third = r.width() / 3
        # organisation (right)
        self._text(p, QRectF(r.right() - third, y, third, self._px(8)), org,
                   Qt.AlignRight | Qt.AlignTop, self.font(12.5, True))
        if meta.get("org_subtitle"):
            self._text(p, QRectF(r.right() - third, y + self._px(8), third, self._px(6)),
                       meta["org_subtitle"], Qt.AlignRight | Qt.AlignTop, self.font(9.5), "#374151")
        # logo (left)
        if self.logo is not None:
            box = QRectF(r.left(), y, third, self._px(18))
            img = self.logo.scaled(int(box.width()), int(box.height()), Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            p.drawImage(QPointF(box.left(), box.top()), img)
        # title (centre)
        self._text(p, QRectF(r.left() + third * 0.5, y + self._px(9), r.width() - third, self._px(9)),
                   self.r.title, Qt.AlignHCenter | Qt.AlignVCenter, self.font(15, True), "#0f2c4c")
        if self.r.subtitle:
            self._text(p, QRectF(r.left() + third * 0.5, y + self._px(18), r.width() - third,
                                 self._px(7)), self.r.subtitle, Qt.AlignHCenter | Qt.AlignVCenter,
                       self.font(10.5), "#1f2937")
        line_y = y + h - self._px(3)
        p.setPen(QPen(QColor("#0f2c4c"), self._px(0.5)))
        p.drawLine(QPointF(r.left(), line_y), QPointF(r.right(), line_y))
        return y + h

    def _col_rects(self, y: float, h: float):
        x = self.page.right()
        for w in self.col_w:
            yield QRectF(x - w, y, w, h)
            x -= w

    def _paint_table_header(self, p: QPainter, y: float) -> float:
        h = self.colhead
        p.setPen(QPen(GRID, self._px(0.25)))
        for c, rect in zip(self.r.columns, self._col_rects(y, h)):
            p.fillRect(rect, HEAD_BG)
            p.setPen(QPen(GRID, self._px(0.25)))
            p.drawRect(rect)
            self._text(p, rect.adjusted(self.pad, self.pad, -self.pad, -self.pad), c.title,
                       Qt.AlignCenter, self.head_font)
        return y + h

    def _paint_row(self, p: QPainter, row: _Row, y: float):
        r = self.page
        pen = QPen(GRID, self._px(0.2))
        if row.kind == "section":
            rect = QRectF(r.left(), y, r.width(), row.height)
            p.fillRect(rect, SECTION_BG)
            p.setPen(pen)
            p.drawRect(rect)
            self._text(p, rect.adjusted(self.pad * 2, self.pad, -self.pad * 2, -self.pad),
                       row.cells[0], Qt.AlignRight | Qt.AlignVCenter, self.head_font, "#0f2c4c")
            return
        bold = row.kind in ("subtotal", "total")
        font = self.head_font if bold else self.body_font
        for i, (c, rect) in enumerate(zip(self.r.columns, self._col_rects(y, row.height))):
            tone = row.tones.get(c.key) or row.tone
            if row.kind == "total":
                p.fillRect(rect, HEAD_BG)
            elif row.kind == "subtotal":
                p.fillRect(rect, SUBTOTAL_BG)
            elif tone in TONE_BG:
                p.fillRect(rect, QColor(TONE_BG[tone]))
            p.setPen(pen)
            p.drawRect(rect)
            align = Qt.AlignCenter if (c.numeric or c.kind in ("time", "date", "datetime")) \
                else Qt.AlignRight | Qt.AlignVCenter
            self._text(p, rect.adjusted(self.pad, self.pad, -self.pad, -self.pad), row.cells[i],
                       align, font, TONE_FG.get(tone, "#111827"))

    def _paint_notes(self, p: QPainter, y: float) -> float:
        r = self.page
        for n in self.r.notes:
            rect = self.fm.boundingRect(QRectF(0, 0, r.width(), 10_000),
                                        int(Qt.TextWordWrap | Qt.AlignRight), n)
            self._text(p, QRectF(r.left(), y, r.width(), rect.height()), n,
                       Qt.AlignRight | Qt.AlignTop, self.body_font, "#374151")
            y += rect.height() + self._px(1)
        return y

    def _paint_signatures(self, p: QPainter, y: float):
        r = self.page
        n = len(self.r.signatures)
        w = r.width() / n
        x = r.right()
        for title in self.r.signatures:
            box = QRectF(x - w, y, w, self._px(18))
            self._text(p, QRectF(box.left(), box.top(), box.width(), self._px(6)), title,
                       Qt.AlignHCenter | Qt.AlignTop, self.font(9.5, True))
            p.setPen(QPen(QColor("#9ca3af"), self._px(0.3), Qt.DotLine))
            p.drawLine(QPointF(box.left() + w * 0.18, box.bottom()),
                       QPointF(box.right() - w * 0.18, box.bottom()))
            self._text(p, QRectF(box.left(), box.bottom() + self._px(0.5), box.width(), self._px(5)),
                       "التوقيع", Qt.AlignHCenter | Qt.AlignTop, self.font(7.5), "#6b7280")
            x -= w

    def _paint_footer(self, p: QPainter, index: int):
        r = self.page
        y = r.bottom() - self.footer_h + self._px(2.5)
        p.setPen(QPen(GRID, self._px(0.25)))
        p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        small = self.font(7.5)
        meta = self.r.meta
        gen = meta.get("generated")
        when = digits(gen.strftime("%Y/%m/%d %H:%M")) if gen else ""
        who = meta.get("user") or ""
        right = f"طُبع في {when}" + (f" بواسطة {who}" if who else "")
        h = self.footer_h - self._px(3)
        self._text(p, QRectF(r.left(), y + self._px(0.5), r.width(), h), right,
                   Qt.AlignRight | Qt.AlignVCenter, small, "#4b5563")
        page_txt = f"صفحة {digits(index + 1)} من {digits(len(self.pages))}"
        self._text(p, QRectF(r.left(), y + self._px(0.5), r.width(), h), page_txt,
                   Qt.AlignHCenter | Qt.AlignVCenter, small, "#4b5563")
        self._text(p, QRectF(r.left(), y + self._px(0.5), r.width(), h), APP_TITLE,
                   Qt.AlignLeft | Qt.AlignVCenter, small, "#9ca3af")


# ----------------------------------------------------------------- output
def page_layout(report: Report) -> QPageLayout:
    orientation = QPageLayout.Landscape if report.landscape else QPageLayout.Portrait
    return QPageLayout(QPageSize(QPageSize.A4), orientation, QMarginsF(10, 10, 10, 10),
                       QPageLayout.Millimeter)


def paint_on(device, report: Report, logo: QImage | None = None, new_page=None) -> int:
    """Render all pages on a QPagedPaintDevice (QPrinter / QPdfWriter)."""
    painter = QPainter(device)
    try:
        dpi = device.logicalDpiX()
        layout = device.pageLayout()
        rect = layout.paintRectPixels(device.resolution())
        area = QRectF(0, 0, rect.width(), rect.height())
        renderer = ReportRenderer(report, logo)
        count = renderer.layout(area, dpi)
        for i in range(count):
            if i:
                (new_page or device.newPage)()
            renderer.paint(painter, i)
        return count
    finally:
        painter.end()


def export_pdf(report: Report, path: str, logo: QImage | None = None) -> int:
    writer = QPdfWriter(str(path))
    writer.setResolution(300)
    writer.setPageLayout(page_layout(report))
    writer.setTitle(report.title)
    writer.setCreator(APP_TITLE)
    return paint_on(writer, report, logo)


def render_images(report: Report, dpi: int = 110, logo: QImage | None = None,
                  layout_dpi: int = 300) -> list[QImage]:
    """Rasterise pages (tests and thumbnails).  Laid out at print resolution and scaled,
    so the pagination is identical to the printed/PDF output."""
    size = QPageSize(QPageSize.A4).sizePoints()
    w_in, h_in = size.width() / 72, size.height() / 72
    if report.landscape:
        w_in, h_in = h_in, w_in
    margin = 10 * MM
    area = QRectF(0, 0, (w_in - 2 * margin) * layout_dpi, (h_in - 2 * margin) * layout_dpi)
    renderer = ReportRenderer(report, logo)
    count = renderer.layout(area, layout_dpi)
    images = []
    for i in range(count):
        img = QImage(int(w_in * dpi), int(h_in * dpi), QImage.Format_RGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.scale(dpi / layout_dpi, dpi / layout_dpi)
        painter.translate(margin * layout_dpi, margin * layout_dpi)
        renderer.paint(painter, i)
        painter.end()
        images.append(img)
    return images
