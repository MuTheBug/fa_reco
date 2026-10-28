"""Render the application icon (hadir/resources/icons/hadir.ico and hadir.png)."""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "hadir" / "resources" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#2a6fc0"))
    grad.setColorAt(1, QColor("#0f2c4c"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)
    pen = QPen(QColor("white"), max(1.5, 16 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m, L = 52 * s, 44 * s
    for (x, y, dx, dy) in ((m, m, 1, 1), (256 * s - m, m, -1, 1), (m, 256 * s - m, 1, -1),
                           (256 * s - m, 256 * s - m, -1, -1)):
        p.drawLine(int(x), int(y), int(x + dx * L), int(y))
        p.drawLine(int(x), int(y), int(x), int(y + dy * L))
    if size >= 32:
        p.drawPoint(int(102 * s), int(112 * s))
        p.drawPoint(int(154 * s), int(112 * s))
        p.drawArc(QRectF(96 * s, 116 * s, 64 * s, 44 * s), 200 * 16, 140 * 16)
    p.setPen(QPen(QColor("#4ade80"), max(1.5, 14 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(int(150 * s), int(206 * s), int(170 * s), int(224 * s))
    p.drawLine(int(170 * s), int(224 * s), int(208 * s), int(184 * s))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main():
    app = QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    blobs = [png_bytes(render(n)) for n in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries, data = b"", b""
    for n, blob in zip(SIZES, blobs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    (OUT / "hadir.ico").write_bytes(header + entries + data)
    render(256).save(str(OUT / "hadir.png"))
    del app
    print("written", OUT / "hadir.ico")


if __name__ == "__main__":
    main()
