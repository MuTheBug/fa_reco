"""Face detection with OpenCV's YuNet."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .models import YUNET, model_path


@dataclass
class Face:
    box: np.ndarray        # x, y, w, h in source-image pixels
    landmarks: np.ndarray  # 5x2: image-left eye, image-right eye, nose, mouth corners
    score: float

    @property
    def area(self) -> float:
        return float(self.box[2] * self.box[3])

    @property
    def eye_distance(self) -> float:
        return float(np.linalg.norm(self.landmarks[0] - self.landmarks[1]))

    @property
    def center(self) -> np.ndarray:
        return self.box[:2] + self.box[2:] / 2


class FaceDetector:
    """YuNet wrapper that runs on a downscaled copy for speed."""

    def __init__(self, score_threshold: float = 0.7, max_side: int = 640):
        self.max_side = max_side
        self._net = cv2.FaceDetectorYN.create(str(model_path(YUNET)), "", (320, 320),
                                              score_threshold, 0.3, 50)
        self._size = None

    def detect(self, image: np.ndarray) -> list[Face]:
        h, w = image.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        small = image if scale == 1.0 else cv2.resize(
            image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        size = (small.shape[1], small.shape[0])
        if size != self._size:
            self._net.setInputSize(size)
            self._size = size
        _, raw = self._net.detect(small)
        faces = []
        if raw is None:
            return faces
        for row in raw:
            box = row[0:4].astype(np.float64) / scale
            lms = row[4:14].reshape(5, 2).astype(np.float64) / scale
            faces.append(Face(box=box, landmarks=lms, score=float(row[14])))
        faces.sort(key=lambda f: f.area, reverse=True)
        return faces
