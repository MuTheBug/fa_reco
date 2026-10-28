"""Test face images, downloaded on demand (not stored in the repository)."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np
import pytest

CACHE = Path(os.environ.get("HADIR_TEST_FACES", Path(__file__).parent / ".cache" / "faces"))
DEEPFACE = "https://raw.githubusercontent.com/serengil/deepface/master/tests/unit/dataset/"
SILENT = ("https://raw.githubusercontent.com/minivision-ai/Silent-Face-Anti-Spoofing/master/"
          "images/sample/")

# identity -> files (labels from deepface's master.csv)
PEOPLE = {
    "A": ["img1.jpg", "img2.jpg", "img4.jpg", "img5.jpg", "img6.jpg", "img7.jpg", "img10.jpg",
          "img11.jpg"],
    "B": ["img13.jpg", "img14.jpg", "img15.jpg"],
    "C": ["img3.jpg", "img12.jpg"],
    "D": ["img20.jpg", "img21.jpg"],
    "E": ["img16.jpg", "img17.jpg"],
    "F": ["img22.jpg", "img23.jpg"],
    "G": ["img24.jpg", "img25.jpg"],
    "H": ["img18.jpg", "img19.jpg"],
    "I": ["img8.jpg", "img9.jpg"],
}


def fetch(name: str, base: str = DEEPFACE) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        try:
            with urllib.request.urlopen(base + name, timeout=60) as r:
                path.write_bytes(r.read())
        except OSError as exc:  # offline
            pytest.skip(f"test image unavailable: {exc}")
    return path


def load(name: str, base: str = DEEPFACE):
    import cv2

    return cv2.imread(str(fetch(name, base)))


def webcam_frames(img, n: int, eye_px: float = 80.0, seed: int = 0, size=(1280, 720),
                  detector=None):
    """Simulate webcam frames: the photo scaled to a typical kiosk face size, jittered,
    with exposure changes and sensor noise."""
    import cv2

    rng = np.random.default_rng(seed)
    face = detector.detect(img)[0]
    base_scale = eye_px / face.eye_distance
    w, h = size
    frames = []
    for _ in range(n):
        s = base_scale * rng.uniform(0.95, 1.05)
        small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else
                           cv2.INTER_LINEAR)
        canvas = np.full((h, w, 3), 90, np.uint8)
        fc = face.center * s
        ox = int(w / 2 - fc[0] + rng.uniform(-15, 15))
        oy = int(h / 2 - fc[1] + rng.uniform(-10, 10))
        x0, y0 = max(0, ox), max(0, oy)
        x1, y1 = min(w, ox + small.shape[1]), min(h, oy + small.shape[0])
        canvas[y0:y1, x0:x1] = small[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
        frame = canvas.astype(np.float32) * rng.uniform(0.85, 1.1) + rng.normal(0, 4, canvas.shape)
        frames.append(np.clip(frame, 0, 255).astype(np.uint8))
    return frames
