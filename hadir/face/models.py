"""Registry of the neural network models used by the program.

Every model is pinned by SHA-256.  Small models are committed under
``models/``; large ones are fetched once by ``tools/fetch_models.py`` (or by
the Windows build) and shipped inside the installer, so the program itself
never needs the internet.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelSpec:
    id: str
    filename: str
    kind: str  # "detector" | "embedder" | "liveness"
    sha256: str
    size: int
    url: str
    license: str
    title: str


ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
ONNX_ZOO = ("https://media.githubusercontent.com/media/onnx/models/main/validated/vision/"
            "body_analysis/arcface/model")

YUNET = ModelSpec(
    id="yunet-2023mar",
    filename="face_detection_yunet_2023mar.onnx",
    kind="detector",
    sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    size=232589,
    url=f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    license="MIT (OpenCV Zoo)",
    title="YuNet face detector",
)

SFACE = ModelSpec(
    id="sface-2021dec",
    filename="face_recognition_sface_2021dec.onnx",
    kind="embedder",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    size=38696353,
    url=f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    license="Apache-2.0 (OpenCV Zoo)",
    title="SFace (MobileFaceNet) 128-d",
)

ARCFACE_R100 = ModelSpec(
    id="arcface-r100",
    filename="arcfaceresnet100-8.onnx",
    kind="embedder",
    sha256="f3a6bc281e72f88862f5748b53be3d76b3b48f8f1ab1f4a537941bdc4e1b01da",
    size=261036388,
    url=f"{ONNX_ZOO}/arcfaceresnet100-8.onnx",
    license="Apache-2.0 (ONNX Model Zoo)",
    title="ArcFace LResNet100E-IR 512-d",
)

LIVENESS_V2 = ModelSpec(
    id="minifasnet-v2-2.7",
    filename="minifasnet_v2_2.7_80x80.onnx",
    kind="liveness",
    # Converted in-repo by tools/convert_minifasnet.py from 2.7_80x80_MiniFASNetV2.pth
    # (sha256 a5eb02e1843f19b5386b953cc4c9f011c3f985d0ee2bb9819eea9a142099bec0).
    sha256="9006d08443747a393c73d2ecc3b8991bf7b1148f5f97336d0c7639dba10e9bd6",
    size=1736992,
    url="",
    license="Apache-2.0 (minivision Silent-Face-Anti-Spoofing)",
    title="MiniFASNetV2 (scale 2.7)",
)

LIVENESS_V1SE = ModelSpec(
    id="minifasnet-v1se-4.0",
    filename="minifasnet_v1se_4.0_80x80.onnx",
    kind="liveness",
    # Converted from 4_0_0_80x80_MiniFASNetV1SE.pth
    # (sha256 84ee1d37d96894d5e82de5a57df044ef80a58be2b218b5ed7cdfd875ec2f5990).
    sha256="0ae0494803edfdba1288aa2ccdbe7dd267bf1c2c247a1f25952a8866cdaad021",
    size=1734544,
    url="",
    license="Apache-2.0 (minivision Silent-Face-Anti-Spoofing)",
    title="MiniFASNetV1SE (scale 4.0)",
)

ALL_MODELS = (YUNET, SFACE, ARCFACE_R100, LIVENESS_V2, LIVENESS_V1SE)
EMBEDDERS = {m.id: m for m in (ARCFACE_R100, SFACE)}


def models_dir() -> Path:
    """Locate the folder holding the model files."""
    env = os.environ.get("HADIR_MODELS_DIR")
    if env:
        return Path(env)
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [exe_dir / "models", Path(getattr(sys, "_MEIPASS", exe_dir)) / "models"]
    candidates.append(Path(__file__).resolve().parents[2] / "models")
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[-1]


def model_path(spec: ModelSpec) -> Path:
    return models_dir() / spec.filename


def is_available(spec: ModelSpec) -> bool:
    return model_path(spec).is_file()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
