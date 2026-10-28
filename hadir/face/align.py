"""Five-point similarity alignment to the standard 112x112 ArcFace template."""

from __future__ import annotations

import cv2
import numpy as np

CHIP_SIZE = 112

# Reference landmark positions (image-left eye, image-right eye, nose tip,
# image-left mouth corner, image-right mouth corner) used by ArcFace/SFace.
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)


def umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity transform (rotation + uniform scale + shift).

    Returns a 2x3 matrix mapping ``src`` points onto ``dst`` points.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_c = src - src_mean
    dst_c = dst - dst_mean
    cov = dst_c.T @ src_c / n
    u, s, vt = np.linalg.svd(cov)
    d = np.ones(2)
    if np.linalg.det(cov) < 0:
        d[-1] = -1
    rot = u @ np.diag(d) @ vt
    var_src = (src_c ** 2).sum() / n
    scale = (s * d).sum() / var_src if var_src > 0 else 1.0
    m = np.zeros((2, 3))
    m[:, :2] = scale * rot
    m[:, 2] = dst_mean - scale * rot @ src_mean
    return m


def align_face(image: np.ndarray, landmarks: np.ndarray, size: int = CHIP_SIZE) -> np.ndarray:
    """Warp ``image`` so the five landmarks land on the ArcFace template."""
    dst = ARCFACE_TEMPLATE * (size / CHIP_SIZE)
    m = umeyama(np.asarray(landmarks, dtype=np.float64).reshape(5, 2), dst)
    return cv2.warpAffine(image, m, (size, size), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
