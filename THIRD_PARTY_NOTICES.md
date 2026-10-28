# Third-party notices — إشعارات المكوّنات الخارجية

The program bundles the following open-source components. License texts are in
the `LICENSES/` folder.

## Neural network models

| Model | File | Source | License |
|---|---|---|---|
| YuNet face detector (2023mar) | `face_detection_yunet_2023mar.onnx` | OpenCV Zoo — https://github.com/opencv/opencv_zoo | MIT |
| SFace face recognition (2021dec) | `face_recognition_sface_2021dec.onnx` | OpenCV Zoo — https://github.com/opencv/opencv_zoo | Apache-2.0 |
| ArcFace LResNet100E-IR | `arcfaceresnet100-8.onnx` | ONNX Model Zoo — https://github.com/onnx/models (validated/vision/body_analysis/arcface) | Apache-2.0 |
| MiniFASNetV2 (scale 2.7), MiniFASNetV1SE (scale 4.0) | `minifasnet_*.onnx` | minivision-ai Silent-Face-Anti-Spoofing — https://github.com/minivision-ai/Silent-Face-Anti-Spoofing | Apache-2.0 |

The MiniFASNet ONNX files were converted from the original PyTorch checkpoints
(`2.7_80x80_MiniFASNetV2.pth`, `4_0_0_80x80_MiniFASNetV1SE.pth`) by
`tools/convert_minifasnet.py` (batch-norm folding only; weights unchanged and
the output verified against a reference implementation).  This is a
modification of the original work as described in section 4 of the Apache
License 2.0.

## Libraries

| Component | License |
|---|---|
| Qt 6 / PySide6 (https://www.qt.io, https://wiki.qt.io/Qt_for_Python) | LGPL-3.0 (dynamically linked; you may replace the Qt libraries in the program folder) |
| OpenCV (https://opencv.org) | Apache-2.0 |
| ONNX Runtime (https://onnxruntime.ai) | MIT |
| NumPy (https://numpy.org) | BSD-3-Clause |
| openpyxl (https://openpyxl.readthedocs.io) | MIT |
| Python (https://www.python.org) | PSF License |

## Fonts

| Font | License |
|---|---|
| Noto Sans Arabic (https://github.com/notofonts/arabic) | SIL Open Font License 1.1 (`hadir/resources/fonts/OFL.txt`) |
