"""Convert the official MiniFASNet anti-spoofing weights (.pth) to ONNX.

Source: https://github.com/minivision-ai/Silent-Face-Anti-Spoofing (Apache-2.0)
  resources/anti_spoof_models/2.7_80x80_MiniFASNetV2.pth
  resources/anti_spoof_models/4_0_0_80x80_MiniFASNetV1SE.pth

The checkpoints use PyTorch's legacy (pre-1.6) serialization, which this script
reads directly with ``pickle`` + ``numpy`` so that PyTorch is not required.
BatchNorm layers are folded into the preceding convolutions.

Model input : float32 [1, 3, 80, 80], BGR, raw 0..255 values (no scaling).
Model output: float32 [1, 3] softmax probabilities; index 1 == real face.

Usage:
    python tools/convert_minifasnet.py SRC.pth OUT.onnx
    python tools/convert_minifasnet.py --verify SRC.pth OUT.onnx IMAGE...
"""

from __future__ import annotations

import argparse
import collections
import pickle
import struct
import sys

import numpy as np

BN_EPS = 1e-5

KEEP = {
    "1.8M": [32, 32, 103, 103, 64, 13, 13, 64, 26, 26,
             64, 13, 13, 64, 52, 52, 64, 231, 231, 128,
             154, 154, 128, 52, 52, 128, 26, 26, 128, 52,
             52, 128, 26, 26, 128, 26, 26, 128, 308, 308,
             128, 26, 26, 128, 26, 26, 128, 512, 512],
    "1.8M_": [32, 32, 103, 103, 64, 13, 13, 64, 13, 13, 64, 13,
              13, 64, 13, 13, 64, 231, 231, 128, 231, 231, 128, 52,
              52, 128, 26, 26, 128, 77, 77, 128, 26, 26, 128, 26, 26,
              128, 308, 308, 128, 26, 26, 128, 26, 26, 128, 512, 512],
}

# model type -> (keep list, uses SE blocks)
ARCH = {
    "MiniFASNetV1": ("1.8M", False),
    "MiniFASNetV2": ("1.8M_", False),
    "MiniFASNetV1SE": ("1.8M", True),
    "MiniFASNetV2SE": ("1.8M_", True),
}

_DTYPES = {
    "FloatStorage": np.float32,
    "DoubleStorage": np.float64,
    "HalfStorage": np.float16,
    "LongStorage": np.int64,
    "IntStorage": np.int32,
    "ShortStorage": np.int16,
    "CharStorage": np.int8,
    "ByteStorage": np.uint8,
    "BoolStorage": np.bool_,
}


# --------------------------------------------------------------------------
# Legacy PyTorch checkpoint reader
# --------------------------------------------------------------------------
class _StorageType:
    def __init__(self, name: str):
        self.dtype = _DTYPES[name]


class _LazyTensor:
    def __init__(self, storage_key, offset, size, stride):
        self.storage_key = storage_key
        self.offset = offset
        self.size = tuple(size)
        self.stride = tuple(stride)


def _rebuild_tensor_v2(storage, storage_offset, size, stride, requires_grad=False,
                       backward_hooks=None, metadata=None):
    return _LazyTensor(storage, storage_offset, size, stride)


class _Unpickler(pickle.Unpickler):
    def __init__(self, f, storages):
        super().__init__(f)
        self._storages = storages

    def find_class(self, module, name):
        if module == "collections" and name == "OrderedDict":
            return collections.OrderedDict
        if module == "torch._utils" and name in ("_rebuild_tensor_v2", "_rebuild_tensor"):
            return _rebuild_tensor_v2
        if module == "torch" and name in _DTYPES:
            return _StorageType(name)
        raise pickle.UnpicklingError(f"unsupported global {module}.{name}")

    def persistent_load(self, pid):
        typename, storage_type, key, _location, numel, view_metadata = pid
        if typename != "storage" or view_metadata is not None:
            raise pickle.UnpicklingError(f"unsupported persistent id {pid!r}")
        self._storages[key] = (storage_type.dtype, numel)
        return key


def load_legacy_state_dict(path: str) -> dict[str, np.ndarray]:
    with open(path, "rb") as f:
        magic = pickle.load(f)
        if magic != 0x1950A86A20F9469CFC6C:
            raise ValueError("not a legacy PyTorch checkpoint")
        protocol = pickle.load(f)
        if protocol != 1001:
            raise ValueError(f"unsupported protocol {protocol}")
        pickle.load(f)  # system info
        storages: dict[str, tuple] = {}
        result = _Unpickler(f, storages).load()
        keys = pickle.load(f)
        data: dict[str, np.ndarray] = {}
        for key in keys:
            dtype, numel = storages[key]
            (count,) = struct.unpack("<q", f.read(8))
            if count != numel:
                raise ValueError("storage size mismatch")
            buf = f.read(count * np.dtype(dtype).itemsize)
            data[key] = np.frombuffer(buf, dtype=dtype).copy()
    out = {}
    for name, t in result.items():
        if not isinstance(t, _LazyTensor):
            continue
        flat = data[t.storage_key]
        itemsize = flat.itemsize
        arr = np.lib.stride_tricks.as_strided(
            flat[t.offset:], shape=t.size, strides=[s * itemsize for s in t.stride])
        out[name[7:] if name.startswith("module.") else name] = np.array(arr)
    return out


# --------------------------------------------------------------------------
# Architecture description (mirrors src/model_lib/MiniFASNet.py)
# --------------------------------------------------------------------------
def build_layers(model_type: str, conv6_kernel: int = 5):
    """Return an ordered list of ops: (kind, prefix, params)."""
    keep_name, use_se = ARCH[model_type]
    k = KEEP[keep_name]
    ops = []

    def conv_block(prefix, stride, pad, groups, act=True):
        ops.append(("conv", prefix, dict(stride=stride, pad=pad, groups=groups, act=act)))

    def depth_wise(prefix, c2_in, stride, residual, se=False):
        ops.append(("begin_res" if residual else "nop", prefix, {}))
        conv_block(prefix + ".conv", 1, 0, 1)
        conv_block(prefix + ".conv_dw", stride, 1, c2_in)
        conv_block(prefix + ".project", 1, 0, 1, act=False)
        if se:
            ops.append(("se", prefix + ".se_module", {}))
        ops.append(("end_res" if residual else "nop", prefix, {}))

    def residual(prefix, starts, se):
        for i, s in enumerate(starts):
            depth_wise(f"{prefix}.model.{i}", k[s + 1], 1, True, se=se and i == len(starts) - 1)

    conv_block("conv1", 2, 1, 1)
    conv_block("conv2_dw", 1, 1, k[1])
    depth_wise("conv_23", k[2], 2, False)
    residual("conv_3", [4, 7, 10, 13], use_se)
    depth_wise("conv_34", k[17], 2, False)
    residual("conv_4", [19, 22, 25, 28, 31, 34], use_se)
    depth_wise("conv_45", k[38], 2, False)
    residual("conv_5", [40, 43], use_se)
    conv_block("conv_6_sep", 1, 0, 1)
    ops.append(("conv", "conv_6_dw", dict(stride=1, pad=0, groups=k[48], act=False,
                                          kernel=conv6_kernel)))
    ops.append(("head", "", {}))
    return ops


def fold_bn(w, sd, bn_prefix):
    gamma = sd[bn_prefix + ".weight"]
    beta = sd[bn_prefix + ".bias"]
    mean = sd[bn_prefix + ".running_mean"]
    var = sd[bn_prefix + ".running_var"]
    scale = gamma / np.sqrt(var + BN_EPS)
    shape = (-1,) + (1,) * (w.ndim - 1)
    return (w * scale.reshape(shape)).astype(np.float32), (beta - mean * scale).astype(np.float32)


# --------------------------------------------------------------------------
# ONNX export
# --------------------------------------------------------------------------
def export_onnx(sd: dict, model_type: str, out_path: str) -> None:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    nodes, inits = [], []
    counter = [0]

    def name(base):
        counter[0] += 1
        return f"{base}_{counter[0]}"

    def init(arr, base):
        n = name(base)
        inits.append(numpy_helper.from_array(np.ascontiguousarray(arr), n))
        return n

    x = "input"
    res_stack = []
    for kind, prefix, p in build_layers(model_type):
        if kind == "conv":
            w = sd[prefix + ".conv.weight"]
            w, b = fold_bn(w, sd, prefix + ".bn")
            kh, kw = w.shape[2], w.shape[3]
            out = name(prefix + "_conv")
            nodes.append(helper.make_node(
                "Conv", [x, init(w, prefix + ".w"), init(b, prefix + ".b")], [out],
                kernel_shape=[kh, kw], strides=[p["stride"]] * 2, pads=[p["pad"]] * 4,
                group=p["groups"]))
            x = out
            if p["act"]:
                slope = sd[prefix + ".prelu.weight"].reshape(-1, 1, 1).astype(np.float32)
                out = name(prefix + "_prelu")
                nodes.append(helper.make_node("PRelu", [x, init(slope, prefix + ".slope")], [out]))
                x = out
        elif kind == "se":
            pooled = name(prefix + "_gap")
            nodes.append(helper.make_node("GlobalAveragePool", [x], [pooled]))
            w1, b1 = fold_bn(sd[prefix + ".fc1.weight"], sd, prefix + ".bn1")
            w2, b2 = fold_bn(sd[prefix + ".fc2.weight"], sd, prefix + ".bn2")
            h = name(prefix + "_fc1")
            nodes.append(helper.make_node("Conv", [pooled, init(w1, prefix + ".w1"),
                                                   init(b1, prefix + ".b1")], [h], kernel_shape=[1, 1]))
            hr = name(prefix + "_relu")
            nodes.append(helper.make_node("Relu", [h], [hr]))
            g = name(prefix + "_fc2")
            nodes.append(helper.make_node("Conv", [hr, init(w2, prefix + ".w2"),
                                                   init(b2, prefix + ".b2")], [g], kernel_shape=[1, 1]))
            gs = name(prefix + "_sig")
            nodes.append(helper.make_node("Sigmoid", [g], [gs]))
            out = name(prefix + "_scale")
            nodes.append(helper.make_node("Mul", [x, gs], [out]))
            x = out
        elif kind == "begin_res":
            res_stack.append(x)
        elif kind == "end_res":
            out = name(prefix + "_add")
            nodes.append(helper.make_node("Add", [res_stack.pop(), x], [out]))
            x = out
        elif kind == "head":
            flat = name("flatten")
            nodes.append(helper.make_node("Flatten", [x], [flat], axis=1))
            lin = sd["linear.weight"]  # (128, 512)
            gamma, beta = sd["bn.weight"], sd["bn.bias"]
            mean, var = sd["bn.running_mean"], sd["bn.running_var"]
            scale = gamma / np.sqrt(var + BN_EPS)
            w1 = (lin.T * scale[None, :]).astype(np.float32)
            b1 = (beta - mean * scale).astype(np.float32)
            emb = name("embedding")
            nodes.append(helper.make_node("Gemm", [flat, init(w1, "lin.w"), init(b1, "lin.b")], [emb]))
            logits = name("logits")
            nodes.append(helper.make_node("Gemm", [emb, init(sd["prob.weight"].T.astype(np.float32),
                                                              "prob.w")], [logits]))
            nodes.append(helper.make_node("Softmax", [logits], ["prob"], axis=1))
    graph = helper.make_graph(
        nodes, f"{model_type}_80x80", [helper.make_tensor_value_info("input", TensorProto.FLOAT,
                                                                     [1, 3, 80, 80])],
        [helper.make_tensor_value_info("prob", TensorProto.FLOAT, [1, 3])], inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)],
                              producer_name="hadir-convert-minifasnet")
    model.ir_version = 7
    model.doc_string = ("MiniFASNet anti-spoofing (minivision-ai/Silent-Face-Anti-Spoofing, "
                        "Apache-2.0). Input BGR 0..255 [1,3,80,80]; output softmax, index 1 = real.")
    onnx.checker.check_model(model)
    onnx.save(model, out_path)


# --------------------------------------------------------------------------
# Plain numpy reference forward (unfused) used to verify the export
# --------------------------------------------------------------------------
def _conv2d(x, w, stride, pad, groups):
    n, c, h, wd = x.shape
    oc, icg, kh, kw = w.shape
    xp = np.pad(x, ((0, 0), (0, 0), (pad, pad), (pad, pad)))
    oh = (h + 2 * pad - kh) // stride + 1
    ow = (wd + 2 * pad - kw) // stride + 1
    out = np.zeros((n, oc, oh, ow), dtype=np.float64)
    ocg = oc // groups
    for g in range(groups):
        xs = xp[:, g * icg:(g + 1) * icg]
        ws = w[g * ocg:(g + 1) * ocg]
        cols = np.empty((n, icg, kh, kw, oh, ow))
        for i in range(kh):
            for j in range(kw):
                cols[:, :, i, j] = xs[:, :, i:i + stride * oh:stride, j:j + stride * ow:stride]
        out[:, g * ocg:(g + 1) * ocg] = np.einsum("nckluv,ockl->nouv", cols, ws)
    return out


def _bn(x, sd, prefix):
    shape = (1, -1) + (1,) * (x.ndim - 2)
    g, b = sd[prefix + ".weight"].reshape(shape), sd[prefix + ".bias"].reshape(shape)
    m, v = sd[prefix + ".running_mean"].reshape(shape), sd[prefix + ".running_var"].reshape(shape)
    return (x - m) / np.sqrt(v + BN_EPS) * g + b


def reference_forward(sd, model_type, x):
    x = x.astype(np.float64)
    res_stack = []
    for kind, prefix, p in build_layers(model_type):
        if kind == "conv":
            x = _conv2d(x, sd[prefix + ".conv.weight"].astype(np.float64), p["stride"], p["pad"],
                        p["groups"])
            x = _bn(x, sd, prefix + ".bn")
            if p["act"]:
                a = sd[prefix + ".prelu.weight"].reshape(1, -1, 1, 1)
                x = np.where(x >= 0, x, x * a)
        elif kind == "se":
            s = x.mean(axis=(2, 3), keepdims=True)
            s = _bn(_conv2d(s, sd[prefix + ".fc1.weight"], 1, 0, 1), sd, prefix + ".bn1")
            s = np.maximum(s, 0)
            s = _bn(_conv2d(s, sd[prefix + ".fc2.weight"], 1, 0, 1), sd, prefix + ".bn2")
            x = x * (1 / (1 + np.exp(-s)))
        elif kind == "begin_res":
            res_stack.append(x)
        elif kind == "end_res":
            x = res_stack.pop() + x
        elif kind == "head":
            x = x.reshape(x.shape[0], -1) @ sd["linear.weight"].T
            x = _bn(x, sd, "bn")
            x = x @ sd["prob.weight"].T
            e = np.exp(x - x.max(axis=1, keepdims=True))
            x = e / e.sum(axis=1, keepdims=True)
    return x


def model_type_from_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].split(".pth")[0].split("_")[-1]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--verify", nargs="*", default=None,
                    help="compare ONNX against the numpy reference (random input + images)")
    args = ap.parse_args(argv)
    model_type = model_type_from_name(args.src)
    sd = load_legacy_state_dict(args.src)
    export_onnx(sd, model_type, args.out)
    print(f"wrote {args.out} ({model_type}, {len(sd)} tensors)")
    if args.verify is not None:
        import onnxruntime as ort
        sess = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
        rng = np.random.default_rng(0)
        inputs = [rng.uniform(0, 255, (1, 3, 80, 80)).astype(np.float32)]
        if args.verify:
            import cv2
            for p in args.verify:
                img = cv2.resize(cv2.imread(p), (80, 80)).astype(np.float32)
                inputs.append(img.transpose(2, 0, 1)[None])
        worst = 0.0
        for inp in inputs:
            got = sess.run(None, {"input": inp})[0]
            ref = reference_forward(sd, model_type, inp)
            worst = max(worst, float(np.abs(got - ref).max()))
        print(f"max |onnx - reference| = {worst:.2e}")
        if worst > 1e-4:
            sys.exit("verification FAILED")


if __name__ == "__main__":
    main()
