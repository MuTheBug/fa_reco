# Models

| File | In git | Size | SHA-256 |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` | yes | 0.2 MB | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `minifasnet_v2_2.7_80x80.onnx` | yes | 1.7 MB | `9006d08443747a393c73d2ecc3b8991bf7b1148f5f97336d0c7639dba10e9bd6` |
| `minifasnet_v1se_4.0_80x80.onnx` | yes | 1.7 MB | `0ae0494803edfdba1288aa2ccdbe7dd267bf1c2c247a1f25952a8866cdaad021` |
| `face_recognition_sface_2021dec.onnx` | no — `python tools/fetch_models.py` | 38.7 MB | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |
| `arcfaceresnet100-8.onnx` | no — `python tools/fetch_models.py` | 261 MB | `f3a6bc281e72f88862f5748b53be3d76b3b48f8f1ab1f4a537941bdc4e1b01da` |

Sources and licenses: see `THIRD_PARTY_NOTICES.md`.  The two large files are
downloaded once (on any computer with internet) and shipped inside the
installer; the program itself never uses the network.

The MiniFASNet files are produced from the official checkpoints with:

```bash
python tools/convert_minifasnet.py 2.7_80x80_MiniFASNetV2.pth models/minifasnet_v2_2.7_80x80.onnx --verify
python tools/convert_minifasnet.py 4_0_0_80x80_MiniFASNetV1SE.pth models/minifasnet_v1se_4.0_80x80.onnx --verify
```
