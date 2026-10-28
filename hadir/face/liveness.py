"""Anti-spoofing: a passive model ensemble plus an active head-turn challenge.

Passive check: two MiniFASNet models look at the face with different amounts
of context (2.7x and 4x the face box) and estimate whether it is a live face,
a printed photo, or a screen replay.

Active check: the person is asked to turn the head towards a random side.  A
real head rotates in 3D, so the nose moves relative to the eyes; a photo or a
tablet tilted in front of the camera keeps the nose at the same relative
position, which the 5-point landmarks expose.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import cv2
import numpy as np

from .detector import Face
from .embedder import make_session
from .models import LIVENESS_V1SE, LIVENESS_V2, is_available, model_path

REAL = 1  # class index of a live face


def _crop(img: np.ndarray, box, scale: float, out: int = 80) -> np.ndarray:
    """Same context crop as the reference implementation (CropImage)."""
    src_h, src_w = img.shape[:2]
    x, y, w, h = [float(v) for v in box]
    w, h = max(w, 1.0), max(h, 1.0)
    scale = min((src_h - 1) / h, (src_w - 1) / w, scale)
    nw, nh = w * scale, h * scale
    cx, cy = x + w / 2, y + h / 2
    left, top, right, bottom = cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2
    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > src_w - 1:
        left -= right - src_w + 1
        right = src_w - 1
    if bottom > src_h - 1:
        top -= bottom - src_h + 1
        bottom = src_h - 1
    patch = img[int(top):int(bottom) + 1, int(left):int(right) + 1]
    return cv2.resize(patch, (out, out))


class PassiveLiveness:
    def __init__(self, threads: int = 1):
        self.models = []
        for spec, scale in ((LIVENESS_V2, 2.7), (LIVENESS_V1SE, 4.0)):
            if is_available(spec):
                self.models.append((scale, make_session(model_path(spec), threads)))

    @property
    def available(self) -> bool:
        return bool(self.models)

    def score(self, image: np.ndarray, face: Face) -> float:
        """Probability (0..1) that the face is live; 1.0 when no model is installed."""
        if not self.models:
            return 1.0
        total = np.zeros(3)
        for scale, sess in self.models:
            x = _crop(image, face.box, scale).astype(np.float32).transpose(2, 0, 1)[None]
            total += sess.run(None, {"input": x})[0][0]
        return float(total[REAL] / len(self.models))


@dataclass
class HeadTurnChallenge:
    """Ask for a head turn towards ``direction`` as seen on the (mirrored) screen."""

    direction: str  # "left" | "right" in display space
    mirror: bool = True
    started: float = 0.0
    timeout: float = 7.0
    # yaw proxy ~= 0.39 * tan(angle): 0.13 needs a clear ~18 degree turn
    min_turn: float = 0.13
    baseline: list = field(default_factory=list)
    hits: int = 0
    result: str = "pending"  # pending | passed | failed

    @classmethod
    def random(cls, mirror: bool, now: float) -> "HeadTurnChallenge":
        return cls(random.choice(("left", "right")), mirror, now)

    @property
    def sign(self) -> int:
        # Raw-image yaw grows when the nose moves towards the image's right edge.  On a
        # mirrored screen the display's left is the image's right.
        return 1 if (self.direction == "left") == self.mirror else -1

    @property
    def prompt(self) -> str:
        return "أدر رأسك قليلاً نحو اليسار ←" if self.direction == "left" else \
            "→ أدر رأسك قليلاً نحو اليمين"

    def update(self, yaw: float | None, now: float) -> str:
        if self.result != "pending":
            return self.result
        if now - self.started > self.timeout:
            self.result = "failed"
            return self.result
        if yaw is None:
            return self.result
        if len(self.baseline) < 2:
            self.baseline.append(yaw)
            return self.result
        base = float(np.median(self.baseline))
        delta = (yaw - base) * self.sign
        if delta >= self.min_turn:
            self.hits += 1
            if self.hits >= 2:
                self.result = "passed"
        elif delta < -self.min_turn:
            # Turned the wrong way: restart the count (a replayed video rarely matches).
            self.hits = 0
        return self.result
