"""The picture sent with the urgent alert about an attempt to cheat the attendance (a colleague's
face shown to the camera on a phone, or a photo): what the camera saw at that moment with the
face marked in red, and what it saw a moment later (usually who was holding the phone).

Drawn with Qt and the bundled Arabic font, like the summary picture, and saved as JPEG with
OpenCV (camera pictures are much smaller that way). Needs a running QGuiApplication.
"""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from ..core.timeutil import fmt_date, fmt_time, weekday_name
from ..fonts import register_fonts
from .card import INK, MUTED, PAGE, RTL, _Painter

WIDTH = 960
MARGIN = 28
RED, RED_2 = "#991b1b", "#dc2626"
HEADER = 150


def _qimage(frame: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _jpeg(img: QImage, quality: int = 85) -> bytes:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, line = img.width(), img.height(), img.bytesPerLine()
    rgb = np.frombuffer(img.constBits(), np.uint8, count=line * h).reshape(h, line)[:, :w * 3]
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb.reshape(h, w, 3), cv2.COLOR_RGB2BGR),
                           [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return buf.tobytes()


def render_alert(frame: np.ndarray, box=None, later: np.ndarray | None = None, when: datetime | None = None,
                 org: str = "") -> bytes:
    """The alert picture as JPEG: a red header, the moment of the attempt, and a moment later."""
    family = register_fonts()
    inner = WIDTH - 2 * MARGIN
    panels = [("لحظة المحاولة: الوجه المعروض أمام الكاميرا محاط بالأحمر", frame, box)]
    if later is not None:
        panels.append(("بعد ثانيتين: من كان يقف أمام الكاميرا", later, None))
    sizes = [inner * f.shape[0] / f.shape[1] for _, f, _ in panels]
    height = int(HEADER + 20 + sum(56 + s + 24 for s in sizes) + 30)
    img = QImage(WIDTH, height, QImage.Format_RGB32)
    img.fill(QColor(PAGE))
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
    p.setLayoutDirection(Qt.RightToLeft)
    d = _Painter(p, family)
    Bold, Medium = QFont.Weight.Bold, QFont.Weight.Medium

    grad = QLinearGradient(0, 0, WIDTH, HEADER)
    grad.setColorAt(0, QColor(RED_2))
    grad.setColorAt(1, QColor(RED))
    p.fillRect(QRectF(0, 0, WIDTH, HEADER), grad)
    d.text(QRectF(MARGIN, 22, inner, 60), "تنبيه طارئ: محاولة تزوير حضور", 42, "white", Bold)
    sub = []
    if when is not None:
        sub.append(f"{weekday_name(when.date())} {fmt_date(when.date())} الساعة {fmt_time(when)}")
    if org:
        sub.append(org)
    d.text(QRectF(MARGIN, 88, inner, 40), " • ".join(sub), 24, "#fee2e2", Medium)

    y = HEADER + 20
    for (title, f, face_box), h in zip(panels, sizes):
        d.text(QRectF(MARGIN, y, inner, 44), title, 24, INK, Bold)
        y += 56
        target = QRectF(MARGIN, y, inner, h)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(target, 14, 14)
        p.setClipPath(clip)
        p.drawImage(target, _qimage(f))
        p.restore()
        if face_box is not None:
            s = inner / f.shape[1]
            x, fy, bw, bh = [float(v) for v in face_box]
            pad = 0.12 * max(bw, bh)
            pen = QPen(QColor(RED_2), 5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(MARGIN + (x - pad) * s, y + (fy - pad) * s, (bw + 2 * pad) * s,
                                     (bh + 2 * pad) * s), 10, 10)
        y += h + 24
    d.text(QRectF(MARGIN, y - 4, inner, 28), "صورة من كاميرا الحضور — برنامج حاضر", 17, MUTED, Medium, RTL)
    p.end()
    return _jpeg(img)
