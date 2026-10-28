"""Webcam capture in a background thread, keeping only the newest frame."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

BACKENDS = {
    "dshow": getattr(cv2, "CAP_DSHOW", 700),
    "msmf": getattr(cv2, "CAP_MSMF", 1400),
    "v4l2": getattr(cv2, "CAP_V4L2", 200),
    "any": cv2.CAP_ANY,
}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def backend_order(choice: str) -> list[int]:
    if choice in BACKENDS and choice != "auto":
        return [BACKENDS[choice]]
    if sys.platform == "win32":
        return [BACKENDS["dshow"], BACKENDS["msmf"], BACKENDS["any"]]
    if sys.platform.startswith("linux"):
        return [BACKENDS["v4l2"], BACKENDS["any"]]
    return [BACKENDS["any"]]


def list_cameras(max_index: int = 5, backend: str = "auto") -> list[int]:
    """Indices of cameras that deliver a frame (slow: opens each device)."""
    found = []
    for i in range(max_index):
        for be in backend_order(backend):
            cap = cv2.VideoCapture(i, be)
            ok = cap.isOpened() and cap.read()[0]
            cap.release()
            if ok:
                found.append(i)
                break
    return found


class CameraSource:
    """Grabs frames continuously; ``latest()`` returns (frame, sequence number).

    ``source`` may be a camera index, a video file, or a folder of images (used
    for demonstrations and automated tests).
    """

    def __init__(self, index: int = 0, backend: str = "auto", width: int = 1280, height: int = 720,
                 source: str = ""):
        self.index = index
        self.backend = backend
        self.width = width
        self.height = height
        self.source = source
        self._frame = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = "stopped"  # stopped | opening | ok | error
        self.error = ""
        self.fps = 0.0
        self.brightness = 0.0

    # ----------------------------------------------------------------- API
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self.state = "stopped"

    def latest(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._frame, self._seq

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------- worker
    def _publish(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._seq += 1

    def _run(self) -> None:
        src = self.source.strip()
        if src and Path(src).is_dir():
            self._run_folder(Path(src))
        elif src and Path(src).is_file():
            self._run_video(src)
        else:
            self._run_camera()

    def _open(self):
        for be in backend_order(self.backend):
            cap = cv2.VideoCapture(self.index, be)
            if not cap.isOpened():
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap, frame
            cap.release()
        return None, None

    def _run_camera(self) -> None:
        while not self._stop.is_set():
            self.state = "opening"
            cap, frame = self._open()
            if cap is None:
                self.state, self.error = "error", "تعذّر فتح الكاميرا. تأكد من توصيلها وأنها غير مستخدمة في برنامج آخر."
                if self._stop.wait(2.0):
                    break
                continue
            self.state, self.error = "ok", ""
            self._publish(frame)
            failures, count, t0 = 0, 0, time.monotonic()
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures > 30:
                        break  # disconnected: reopen
                    time.sleep(0.02)
                    continue
                failures = 0
                self._publish(frame)
                count += 1
                if count % 15 == 0:
                    now = time.monotonic()
                    self.fps = 15 / max(now - t0, 1e-6)
                    t0 = now
                    self.brightness = float(cv2.cvtColor(frame[::8, ::8], cv2.COLOR_BGR2GRAY).mean())
            cap.release()
            if not self._stop.is_set():
                self.state, self.error = "error", "انقطع اتصال الكاميرا، جارٍ إعادة المحاولة…"
                self._stop.wait(1.0)

    def _run_video(self, path: str) -> None:
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        self.state = "ok"
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self._publish(frame)
            self._stop.wait(1.0 / fps)
        cap.release()

    def _run_folder(self, folder: Path) -> None:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)
        if not files:
            self.state, self.error = "error", "لا توجد صور في المجلد المحدد."
            return
        self.state = "ok"
        i = 0
        while not self._stop.is_set():
            img = cv2.imread(str(files[(i // 10) % len(files)]))
            if img is not None:
                self._publish(img)
            i += 1
            self._stop.wait(0.1)
