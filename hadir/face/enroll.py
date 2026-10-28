"""Guided face enrollment: several poses, quality-checked, same-person checked."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .pipeline import FaceEngine

# (pose key, Arabic instruction, number of samples)
STEPS = [
    ("front", "انظر مباشرة إلى الكاميرا", 4),
    ("left", "أدر وجهك قليلاً نحو اليسار ←", 2),
    ("right", "→ أدر وجهك قليلاً نحو اليمين", 2),
    ("up", "ارفع ذقنك قليلاً ↑", 2),
    ("down", "اخفض ذقنك قليلاً ↓", 2),
    ("front", "انظر إلى الكاميرا مرة أخرى", 2),
]

SAME_PERSON = 0.40  # minimum primary similarity to the first frontal capture
NEAR_DUPLICATE = 0.985  # skip frames identical to one already captured


@dataclass
class Capture:
    pose: str
    chip: np.ndarray
    quality: float
    emb: dict  # model id -> flip-averaged embedding


@dataclass
class EnrollStatus:
    step: int
    total_steps: int
    message: str
    tone: str  # busy | ok | warn | bad
    captured: int
    needed: int
    box: np.ndarray | None = None
    landmarks: np.ndarray | None = None
    done: bool = False
    pose_hint: str = ""


@dataclass
class EnrollmentSession:
    engine: FaceEngine
    mirror: bool = True
    min_eye_px: float = 40
    clock: object = time.monotonic
    check_liveness: bool = True  # refuse printed photos / screens in front of the camera
    step: int = 0
    captures: list = field(default_factory=list)
    _last_capture: float = 0.0
    _step_count: int = 0
    _yaw0: float | None = None
    _pitch0: float | None = None
    _front_pose: list = field(default_factory=list)

    @property
    def needed(self) -> int:
        return sum(n for _, _, n in STEPS)

    @property
    def done(self) -> bool:
        return self.step >= len(STEPS)

    def skip_step(self) -> None:
        if not self.done and STEPS[self.step][0] != "front":
            self._advance()

    def _advance(self):
        self.step += 1
        self._step_count = 0

    def _pose_ok(self, pose: str, yaw: float, pitch: float) -> bool:
        yaw0 = self._yaw0 if self._yaw0 is not None else 0.0
        pitch0 = self._pitch0 if self._pitch0 is not None else 0.6
        dy, dp = yaw - yaw0, pitch - pitch0
        side = 1 if self.mirror else -1  # display "left" == raw +yaw when mirrored
        if pose == "front":
            return abs(yaw) < 0.12 and abs(pitch - 0.6) < 0.16
        if pose == "left":
            return 0.09 <= side * dy <= 0.40 and abs(dp) < 0.12
        if pose == "right":
            return 0.09 <= -side * dy <= 0.40 and abs(dp) < 0.12
        if pose == "up":
            return dp <= -0.045 and abs(dy) < 0.15
        if pose == "down":
            return dp >= 0.045 and abs(dy) < 0.15
        return False

    def feed(self, frame: np.ndarray) -> EnrollStatus:
        now = self.clock()
        total = len(STEPS)
        if self.done:
            return EnrollStatus(total, total, "اكتمل التسجيل", "ok", len(self.captures),
                                self.needed, done=True)
        pose, instruction, count = STEPS[self.step]
        st = EnrollStatus(self.step, total, instruction, "busy", len(self.captures), self.needed,
                          pose_hint=pose)
        a = self.engine.analyze(frame, min_eye_px=self.min_eye_px * 1.25, embed=False, live=False,
                                strict=True, check_pose=pose == "front")
        if a.face is None:
            st.message, st.tone = "لا يظهر وجه أمام الكاميرا", "warn"
            return st
        st.box, st.landmarks = a.face.box, a.face.landmarks
        if a.crowded:
            st.message, st.tone = "يجب أن يظهر شخص واحد فقط", "warn"
            return st
        q = a.quality
        if not q.ok:
            st.message, st.tone = q.reason, "warn"
            return st
        if not self._pose_ok(pose, q.yaw, q.pitch):
            st.tone = "busy"
            return st
        if now - self._last_capture < 0.35:
            return st
        emb = self.engine.embed_all(a.chip, flip=True)
        pid = self.engine.primary.id
        if self.captures:
            ref = self.captures[0].emb[pid]
            if float(emb[pid] @ ref) < SAME_PERSON:
                st.message, st.tone = "يجب أن يبقى الشخص نفسه أمام الكاميرا", "bad"
                return st
            if max(float(emb[pid] @ c.emb[pid]) for c in self.captures) > NEAR_DUPLICATE:
                return st
        if pose == "front" and self.check_liveness and self.engine.liveness.available:
            live = self.engine.liveness.score(frame, a.face)
            if live < 0.2:
                st.message, st.tone = "يبدو أنها صورة وليست وجهاً حقيقياً", "bad"
                return st
        self.captures.append(Capture(pose, a.chip.copy(), q.score, emb))
        self._last_capture = now
        self._step_count += 1
        if pose == "front" and self.step == 0:
            self._front_pose.append((q.yaw, q.pitch))
            self._yaw0 = float(np.median([p[0] for p in self._front_pose]))
            self._pitch0 = float(np.median([p[1] for p in self._front_pose]))
        st.captured = len(self.captures)
        st.tone = "ok"
        if self._step_count >= count:
            self._advance()
            if self.done:
                st.done, st.message = True, "اكتمل التسجيل"
        return st

    def add_photo(self, image: np.ndarray) -> str | None:
        """Add a sample from a still photo.  Returns an error message or None."""
        a = self.engine.analyze(image, min_eye_px=30, embed=False, live=False)
        if a.face is None:
            return "لم يُعثر على وجه في الصورة."
        if a.crowded:
            return "في الصورة أكثر من وجه؛ اختر صورة لشخص واحد."
        if a.quality.eye_px < 30 or a.quality.sharpness < 15:
            return "الصورة صغيرة أو غير واضحة."
        emb = self.engine.embed_all(a.chip, flip=True)
        pid = self.engine.primary.id
        if self.captures and float(emb[pid] @ self.captures[0].emb[pid]) < SAME_PERSON:
            return "الوجه في الصورة لا يطابق العينات الأخرى لهذا الموظف."
        self.captures.append(Capture("photo", a.chip.copy(), a.quality.score, emb))
        return None

    def final_samples(self) -> list[Capture]:
        """Drop outliers (far from the identity centre) and return the samples to save."""
        if len(self.captures) < 3:
            return list(self.captures)
        pid = self.engine.primary.id
        mat = np.stack([c.emb[pid] for c in self.captures])
        centre = mat.mean(axis=0)
        centre /= np.linalg.norm(centre)
        sims = mat @ centre
        keep = [c for c, s in zip(self.captures, sims) if s >= max(0.45, float(np.median(sims)) - 0.25)]
        return keep


def chip_png(chip: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", chip)
    return buf.tobytes()


def chip_from_png(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
