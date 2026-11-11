"""The attendance summary drawn as a picture for Telegram (no names, only numbers).

Drawn with Qt and the bundled Arabic font, so it looks the same on every computer and needs
nothing from the internet. Needs a running QGuiApplication (the program always has one).
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from ..core.timeutil import digits, fmt_time
from ..fonts import register_fonts
from .summary import ABSENCE_KINDS, Counts, DaySummary, minutes_text, percent

WIDTH = 1080
MARGIN = 40
NAVY, NAVY_2 = "#0f2c4c", "#1d4a7a"
INK, MUTED, LINE, PAGE = "#0f172a", "#64748b", "#e2e8f0", "#eef2f7"
GREEN, AMBER, RED, BLUE, GRAY = "#16a34a", "#d97706", "#dc2626", "#2563eb", "#94a3b8"
SEGMENTS = (("on_time", "في الوقت", GREEN), ("late", "متأخرون", AMBER), ("absent", "غائبون", RED),
            ("leave", "إجازة", BLUE), ("pending", "لم يصلوا بعد", GRAY))
# absolute: the painter lays text out right to left, which would otherwise mirror left and right
RTL = Qt.AlignRight | Qt.AlignVCenter | Qt.AlignAbsolute
LTR = Qt.AlignLeft | Qt.AlignVCenter | Qt.AlignAbsolute
CENTER = Qt.AlignCenter


class _Painter:
    def __init__(self, p: QPainter, family: str):
        self.p, self.family = p, family

    def font(self, px: int, weight=QFont.Weight.Normal) -> QFont:
        f = QFont(self.family)
        f.setPixelSize(px)
        f.setWeight(weight)
        return f

    def text(self, rect: QRectF, text: str, px: int, color: str = INK, weight=QFont.Weight.Normal,
             align=RTL) -> None:
        self.p.setFont(self.font(px, weight))
        self.p.setPen(QColor(color))
        self.p.drawText(rect, int(align), text)

    def card(self, rect: QRectF, radius: float = 26, fill: str = "white") -> None:
        self.p.setPen(Qt.NoPen)
        self.p.setBrush(QColor(0, 0, 0, 14))
        self.p.drawRoundedRect(rect.translated(0, 4), radius, radius)  # a soft shadow
        self.p.setBrush(QColor(fill))
        self.p.drawRoundedRect(rect, radius, radius)

    def bar(self, rect: QRectF, counts: Counts) -> None:
        """A horizontal bar split by status, drawn from the right."""
        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        self.p.save()
        self.p.setClipPath(path)
        self.p.fillRect(rect, QColor(LINE))
        right = rect.right()
        total = max(counts.due, 1)
        for key, _, color in SEGMENTS:
            width = rect.width() * getattr(counts, key) / total
            if width > 0:
                self.p.fillRect(QRectF(right - width, rect.top(), width, rect.height()), QColor(color))
                right -= width
        self.p.restore()

    def ring(self, center: QPointF, radius: float, thickness: float, counts: Counts) -> None:
        rect = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
        self.p.setBrush(Qt.NoBrush)
        self.p.setPen(QPen(QColor(LINE), thickness, Qt.SolidLine, Qt.FlatCap))
        self.p.drawEllipse(rect)
        start = 90.0  # from the top, clockwise
        total = max(counts.due, 1)
        for key, _, color in SEGMENTS:
            span = 360.0 * getattr(counts, key) / total
            if span > 0:
                self.p.setPen(QPen(QColor(color), thickness, Qt.SolidLine, Qt.FlatCap))
                self.p.drawArc(rect, int(start * 16), int(-span * 16))
                start -= span


def _height(s: DaySummary) -> int:
    t = s.total
    details = 190 if (t.absent or t.early or t.left_on_leave or t.late) else 0
    # header and ring card, tiles, details, groups card, colour key
    return 530 + 230 + details + 70 + len(s.groups) * 132 + 24 + 38 + 30


def render(s: DaySummary) -> bytes:
    """The summary as a PNG picture."""
    family = register_fonts()
    height = _height(s)
    img = QImage(WIDTH, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(PAGE))
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    p.setLayoutDirection(Qt.RightToLeft)
    d = _Painter(p, family)
    t = s.total
    inner = WIDTH - 2 * MARGIN
    Bold, Medium = QFont.Weight.Bold, QFont.Weight.Medium

    # header
    grad = QLinearGradient(0, 0, WIDTH, 230)
    grad.setColorAt(0, QColor(NAVY_2))
    grad.setColorAt(1, QColor(NAVY))
    p.fillRect(QRectF(0, 0, WIDTH, 230), grad)
    title = QRectF(WIDTH - MARGIN - inner * 0.62, 38, inner * 0.62, 70)
    d.text(title, "ملخص الدوام", 56, "white", Bold)
    d.text(QRectF(title.left(), 112, title.width(), 50), s.org, 30, "#c7d6e8", Medium)
    d.text(QRectF(MARGIN, 44, inner * 0.38, 50), s.when, 30, "white", Bold, LTR)
    if s.at is not None:
        d.text(QRectF(MARGIN, 98, inner * 0.38, 44), f"حتى الساعة {fmt_time(s.at)}", 26, "#c7d6e8", Medium, LTR)
    y = 230 + 30

    # the day in one picture: a ring by status, and how many came
    hero = QRectF(MARGIN, y - 60, inner, 300)
    d.card(hero)
    d.ring(QPointF(MARGIN + 175, hero.center().y()), 112, 34, t)
    d.text(QRectF(MARGIN + 63, hero.center().y() - 46, 224, 60), percent(t.rate), 50, INK, Bold, CENTER)
    d.text(QRectF(MARGIN + 63, hero.center().y() + 14, 224, 36), "نسبة الحضور", 22, MUTED, Medium, CENTER)
    right = QRectF(MARGIN + 360, hero.top() + 50, inner - 400, 60)
    d.text(right, f"حضر {digits(t.present)} من {digits(t.due)}", 50, INK, Bold)
    d.text(right.translated(0, 70), "المطلوب حضورهم اليوم", 26, MUTED, Medium)
    extra = []
    if t.off:
        extra.append(f"في عطلة أو استراحة: {digits(t.off)}")
    if t.pending:
        extra.append(f"لم يصلوا بعد: {digits(t.pending)}")
    if extra:
        d.text(right.translated(0, 130), " • ".join(extra), 24, MUTED)
    y = hero.bottom() + 30

    # four (or five) numbers
    tiles = [("on_time", "في الوقت", GREEN, ""),
             ("late", "متأخرون", AMBER,
              f"معدل {minutes_text(round(t.late_minutes / t.late))}" if t.late else ""),
             ("absent", "غائبون", RED, ""), ("leave", "في إجازة", BLUE, "")]
    if t.pending:
        tiles.append(("pending", "لم يصلوا بعد", GRAY, ""))
    gap = 18
    w = (inner - gap * (len(tiles) - 1)) / len(tiles)
    for i, (key, label, color, note) in enumerate(tiles):
        r = QRectF(WIDTH - MARGIN - (i + 1) * w - i * gap, y, w, 200)
        d.card(r, 24)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(r.left() + 24, r.top() + 22, r.width() - 48, 8), 4, 4)
        d.text(QRectF(r.left(), r.top() + 40, r.width(), 84), digits(getattr(t, key)), 72, color, Bold, CENTER)
        d.text(QRectF(r.left(), r.top() + 122, r.width(), 40), label, 26, INK, Medium, CENTER)
        if note:
            d.text(QRectF(r.left(), r.top() + 158, r.width(), 32), note, 20, MUTED, align=CENTER)
    y += 200 + 30

    # what the absences were, and leaving early
    if t.absent or t.early or t.left_on_leave or t.late:
        r = QRectF(MARGIN, y, inner, 160)
        d.card(r)
        d.text(QRectF(MARGIN + 30, y + 22, inner - 60, 40), "تفاصيل", 28, INK, Bold)
        parts = [f"{label}: {digits(t.absences[k])}" for k, label in ABSENCE_KINDS.items() if t.absences.get(k)]
        d.text(QRectF(MARGIN + 30, y + 68, inner - 60, 36),
               ("الغياب — " + " • ".join(parts)) if parts else "لا غياب", 24, RED if parts else GREEN, Medium)
        more = []
        if t.late:
            more.append(f"مجموع التأخير {minutes_text(t.late_minutes)}")
        more.append(f"خروج مبكر: {digits(t.early)}")
        if t.left_on_leave:
            more.append(f"غادروا بإجازة ساعية: {digits(t.left_on_leave)}")
        d.text(QRectF(MARGIN + 30, y + 108, inner - 60, 36), " • ".join(more), 24, MUTED)
        y += 160 + 30

    # by group
    r = QRectF(MARGIN, y, inner, 70 + len(s.groups) * 132)
    d.card(r)
    d.text(QRectF(MARGIN + 30, y + 22, inner - 60, 40), "حسب المجموعة", 28, INK, Bold)
    gy = y + 78
    for name, c in s.groups:
        row = QRectF(MARGIN + 30, gy, inner - 60, 40)
        d.text(row, name, 27, INK, Bold)
        if c.due:
            d.text(row, f"حضر {digits(c.present)} من {digits(c.due)}", 25, INK, Medium, LTR)
            d.bar(QRectF(MARGIN + 30, gy + 48, inner - 60, 22), c)
            legend = [f"{label} {digits(getattr(c, key))}" for key, label, _ in SEGMENTS if getattr(c, key)]
            d.text(QRectF(MARGIN + 30, gy + 76, inner - 60, 34), " • ".join(legend), 21, MUTED)
        else:
            d.text(row, "لا دوام اليوم", 24, MUTED, Medium, LTR)
            d.text(QRectF(MARGIN + 30, gy + 48, inner - 60, 34),
                   f"في عطلة أو استراحة: {digits(c.off)}", 21, MUTED)
        gy += 132
    y = r.bottom() + 24

    # colours and source
    lx = WIDTH - MARGIN
    for _, label, color in SEGMENTS:
        p.setFont(d.font(20, Medium))
        width = p.fontMetrics().horizontalAdvance(label)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(lx - 16, y + 12, 14, 14))
        d.text(QRectF(lx - 24 - width, y, width, 38), label, 20, MUTED, Medium)
        lx -= 24 + width + 26
    d.text(QRectF(MARGIN, y, 300, 38), "حاضر — الحضور ببصمة الوجه", 18, GRAY, align=LTR)
    p.end()

    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)
