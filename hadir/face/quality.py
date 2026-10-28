"""Face image quality and head-pose estimates from the five landmarks."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .detector import Face

LEVEL_PITCH = 0.60  # measured on frontal faces


@dataclass
class Quality:
    eye_px: float
    yaw: float  # nose offset from the eye midpoint / eye distance (+ = towards image right)
    pitch: float  # (nose - eyes) / (mouth - eyes) vertical ratio; ~0.6 when level
    roll: float  # degrees
    sharpness: float
    brightness: float
    contrast: float
    det_score: float
    ok: bool = True
    reason: str = ""

    @property
    def score(self) -> float:
        """0..1 figure used to pick the best frames."""
        s_size = min(1.0, self.eye_px / 90.0)
        s_pose = max(0.0, 1.0 - abs(self.yaw) / 0.45) * max(0.0, 1.0 - abs(self.pitch - LEVEL_PITCH) / 0.35)
        s_sharp = min(1.0, self.sharpness / 300.0)
        s_light = max(0.0, 1.0 - abs(self.brightness - 125.0) / 125.0)
        return float(0.25 * s_size + 0.35 * s_pose + 0.25 * s_sharp + 0.15 * s_light)


def head_pose(lm: np.ndarray) -> tuple[float, float, float]:
    re, le, nose, rm, lm_ = lm[0], lm[1], lm[2], lm[3], lm[4]
    eye_mid = (re + le) / 2
    mouth_mid = (rm + lm_) / 2
    ed = float(np.linalg.norm(le - re)) or 1.0
    roll = math.degrees(math.atan2(le[1] - re[1], le[0] - re[0]))
    # Express the nose in the eye-aligned frame so roll does not leak into yaw.
    c, s = math.cos(math.radians(-roll)), math.sin(math.radians(-roll))
    rot = np.array([[c, -s], [s, c]])
    n = rot @ (nose - eye_mid)
    m = rot @ (mouth_mid - eye_mid)
    yaw = float(n[0] / ed)
    pitch = float(n[1] / m[1]) if abs(m[1]) > 1e-6 else 0.5
    return yaw, pitch, roll


def sharpness(chip_gray: np.ndarray) -> float:
    core = chip_gray[16:104, 16:96]
    return float(cv2.Laplacian(core, cv2.CV_64F).var())


def assess(face: Face, chip: np.ndarray, min_eye_px: float = 40.0, strict: bool = False,
           check_pose: bool = True) -> Quality:
    """Measure quality and decide if the frame is usable (with an Arabic hint if not)."""
    yaw, pitch, roll = head_pose(face.landmarks)
    gray = cv2.cvtColor(chip, cv2.COLOR_BGR2GRAY)
    center = gray[24:100, 20:92]
    q = Quality(eye_px=face.eye_distance, yaw=yaw, pitch=pitch, roll=roll,
                sharpness=sharpness(gray), brightness=float(center.mean()),
                contrast=float(center.std()), det_score=face.score)
    min_eye = min_eye_px * (1.25 if strict else 1.0)
    max_yaw = 0.30 if strict else 0.40
    if q.eye_px < min_eye:
        q.ok, q.reason = False, "اقترب من الكاميرا قليلاً"
    elif q.brightness < (60 if strict else 40):
        q.ok, q.reason = False, "الإضاءة ضعيفة: قف في مكان أكثر إضاءة"
    elif q.brightness > (215 if strict else 235):
        q.ok, q.reason = False, "الإضاءة قوية جداً على الوجه"
    elif q.contrast < 12:
        q.ok, q.reason = False, "الصورة باهتة: حسّن الإضاءة"
    elif check_pose and (abs(q.yaw) > max_yaw
                         or not (LEVEL_PITCH - 0.30 < q.pitch < LEVEL_PITCH + 0.28)):
        q.ok, q.reason = False, "انظر مباشرة إلى الكاميرا"
    elif abs(q.roll) > 30:
        q.ok, q.reason = False, "اجعل رأسك مستقيماً"
    elif q.sharpness < (45 if strict else 25):
        q.ok, q.reason = False, "ثبّت رأسك ولا تتحرك"
    elif q.det_score < 0.6:
        q.ok, q.reason = False, "لا يظهر الوجه بوضوح"
    return q
