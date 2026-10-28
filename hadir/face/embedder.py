"""Face embedding extraction with ONNX Runtime (ArcFace-style models)."""

from __future__ import annotations

import os

import numpy as np

from .models import ModelSpec, model_path


def l2_normalize(x: np.ndarray, axis: int = -1) -> np.ndarray:
    n = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.maximum(n, 1e-12)


def make_session(path, threads: int | None = None):
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = threads or max(1, min(4, os.cpu_count() or 1))
    opts.inter_op_num_threads = 1
    opts.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])


class Embedder:
    """Computes L2-normalised embeddings from aligned 112x112 BGR chips.

    The supported models normalise internally ((x-127.5)/128) and expect RGB.
    With ``flip`` the embedding of the mirrored chip is added before
    normalisation, a standard test-time augmentation that improves robustness.
    """

    def __init__(self, spec: ModelSpec, threads: int | None = None):
        self.spec = spec
        self.session = make_session(model_path(spec), threads)
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.batch_fixed = isinstance(inp.shape[0], int)
        self.dim = int(self.session.get_outputs()[0].shape[-1])

    @property
    def id(self) -> str:
        return self.spec.id

    def _run(self, batch: np.ndarray) -> np.ndarray:
        if self.batch_fixed and batch.shape[0] != 1:
            return np.concatenate([self.session.run(None, {self.input_name: b[None]})[0]
                                   for b in batch])
        return self.session.run(None, {self.input_name: batch})[0]

    def embed(self, chips: list[np.ndarray] | np.ndarray, flip: bool = True) -> np.ndarray:
        chips = np.asarray(chips)
        if chips.ndim == 3:
            chips = chips[None]
        x = chips[..., ::-1].transpose(0, 3, 1, 2).astype(np.float32)  # BGR->RGB, NCHW
        feats = self._run(np.ascontiguousarray(x))
        if flip:
            feats = feats + self._run(np.ascontiguousarray(x[..., ::-1]))
        return l2_normalize(feats.astype(np.float32))
