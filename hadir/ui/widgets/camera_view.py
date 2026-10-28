"""Live camera view with the recognition overlay."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..theme import FONT_FAMILY

TONES = {
    "idle": QColor(255, 255, 255, 210),
    "busy": QColor("#3b82f6"),
    "ok": QColor("#16a34a"),
    "bad": QColor("#dc2626"),
    "warn": QColor("#f59e0b"),
}


class CameraView(QWidget):
    def __init__(self, parent=None, mirror: bool = True, show_guide: bool = True):
        super().__init__(parent)
        self.mirror = mirror
        self.show_guide = show_guide
        self.fill_light = False
        self._img: QImage | None = None
        self._src_size = (1280, 720)
        self.status = None
        self.message = ""
        self.placeholder = "جارٍ تشغيل الكاميرا…"
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 240)

    # -------------------------------------------------------------- input
    def set_frame(self, frame: np.ndarray | None) -> None:
        if frame is None:
            self._img = None
            self.update()
            return
        h, w = frame.shape[:2]
        self._src_size = (w, h)
        target = self._target_rect(w, h)
        tw, th = max(1, int(target.width())), max(1, int(target.height()))
        small = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA if tw < w else cv2.INTER_LINEAR)
        if self.mirror:
            small = cv2.flip(small, 1)
        rgb = np.ascontiguousarray(small)
        self._img = QImage(rgb.data, tw, th, 3 * tw, QImage.Format_BGR888).copy()
        self.update()

    def set_status(self, status) -> None:
        self.status = status
        self.update()

    # ------------------------------------------------------------- layout
    def _target_rect(self, w: int, h: int) -> QRectF:
        margin = 14 if self.fill_light else 0
        avail = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)
        scale = min(avail.width() / w, avail.height() / h)
        tw, th = w * scale, h * scale
        return QRectF(avail.center().x() - tw / 2, avail.center().y() - th / 2, tw, th)

    def _map_box(self, box, target: QRectF) -> QRectF:
        w, h = self._src_size
        sx, sy = target.width() / w, target.height() / h
        x, y, bw, bh = [float(v) for v in box]
        if self.mirror:
            x = w - (x + bw)
        return QRectF(target.left() + x * sx, target.top() + y * sy, bw * sx, bh * sy)

    # -------------------------------------------------------------- paint
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        p.fillRect(r, QColor("white") if self.fill_light else QColor("#0b1d30"))
        if self._img is None:
            p.setPen(QColor("#9fb3c8") if not self.fill_light else QColor("#475569"))
            f = QFont(FONT_FAMILY)
            f.setPointSize(16)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, self.placeholder)
            return
        target = self._target_rect(*self._src_size)
        path = QPainterPath()
        path.addRoundedRect(target, 18, 18)
        p.setClipPath(path)
        p.drawImage(target, self._img)
        st = self.status
        tone = TONES.get(getattr(st, "tone", "idle"), TONES["idle"])

        if self.show_guide:
            ow, oh = target.height() * 0.52, target.height() * 0.70
            oval = QRectF(target.center().x() - ow / 2, target.center().y() - oh / 2 - target.height() * 0.03,
                          ow, oh)
            shade = QPainterPath()
            shade.addRect(target)
            hole = QPainterPath()
            hole.addEllipse(oval)
            p.fillPath(shade.subtracted(hole), QColor(8, 20, 35, 95))
            p.setPen(QPen(tone, 4))
            p.drawEllipse(oval)
            progress = getattr(st, "progress", 0.0) or 0.0
            if st is not None and st.state in ("scanning", "challenge") and progress > 0:
                p.setPen(QPen(tone, 9, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(oval.adjusted(-8, -8, 8, 8), 90 * 16, int(-360 * 16 * progress))
        if st is not None and st.box is not None and st.state != "idle":
            box = self._map_box(st.box, target)
            p.setPen(QPen(tone, 3))
            p.setBrush(Qt.NoBrush)
            self._corners(p, box)
        if st is not None and st.challenge:
            self._arrow(p, target, st.challenge)
        p.setClipping(False)
        msg = getattr(st, "message", "") if st is not None else ""
        if msg:
            self._pill(p, target, msg, tone)

    def _corners(self, p: QPainter, b: QRectF):
        L = min(b.width(), b.height()) * 0.22
        for (x, y, dx, dy) in ((b.left(), b.top(), 1, 1), (b.right(), b.top(), -1, 1),
                               (b.left(), b.bottom(), 1, -1), (b.right(), b.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * L, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * L))

    def _arrow(self, p: QPainter, target: QRectF, direction: str):
        # direction is in display space: "left" points to the left edge of the screen
        cx = target.left() + target.width() * (0.16 if direction == "left" else 0.84)
        cy = target.center().y()
        s = target.height() * 0.10
        sign = -1 if direction == "left" else 1
        path = QPainterPath()
        path.moveTo(cx + sign * s, cy)
        path.lineTo(cx - sign * s * 0.2, cy - s)
        path.lineTo(cx - sign * s * 0.2, cy - s * 0.45)
        path.lineTo(cx - sign * s * 1.2, cy - s * 0.45)
        path.lineTo(cx - sign * s * 1.2, cy + s * 0.45)
        path.lineTo(cx - sign * s * 0.2, cy + s * 0.45)
        path.lineTo(cx - sign * s * 0.2, cy + s)
        path.closeSubpath()
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(QBrush(QColor("#f59e0b")))
        p.drawPath(path)

    def _pill(self, p: QPainter, target: QRectF, text: str, tone: QColor):
        f = QFont(FONT_FAMILY)
        f.setPointSizeF(max(13.0, target.height() / 32))
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        w = min(target.width() - 40, fm.horizontalAdvance(text) + 56)
        h = fm.height() + 18
        rect = QRectF(target.center().x() - w / 2, target.bottom() - h - 18, w, h)
        bg = QColor(tone)
        if tone == TONES["idle"]:
            bg = QColor(15, 44, 76, 220)
        bg.setAlpha(225)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(QColor("white"))
        p.drawText(rect, Qt.AlignCenter, text)
