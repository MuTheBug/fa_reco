"""Webcam capture in a background thread, keeping only the newest frame.

A laptop at the door is often closed without being shut down. When it wakes up, the camera
handle it had is usually dead: reading it fails, hangs, or keeps returning the same (often
black) picture. The capture thread reopens the camera when reads fail or the picture stops
changing, and ``watch()`` (called every few seconds by the screen) starts a new capture thread
when frames stop arriving altogether, or when the one opening the camera hangs.
"""

from __future__ import annotations

import logging
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
STALL_SECONDS = 8.0  # no new frame for this long while running: start the camera again
OPEN_SECONDS = 30.0  # opening the camera takes this long: the driver is stuck, start again
FROZEN_SECONDS = 3.0  # the same picture for this long: frozen or black, reopen the camera
MSG_OPENING = "جارٍ تشغيل الكاميرا…"
MSG_RESTART = "جارٍ إعادة تشغيل الكاميرا…"
MSG_CANNOT_OPEN = "تعذّر فتح الكاميرا. تأكد من توصيلها وأنها غير مستخدمة في برنامج آخر."
MSG_LOST = "انقطع اتصال الكاميرا، جارٍ إعادة المحاولة…"
MSG_FROZEN = "الكاميرا لا تعطي صورة متجددة (هل غطاء العدسة مغلق؟)، جارٍ إعادة تشغيلها…"

log = logging.getLogger("hadir")


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


def _signature(frame: np.ndarray) -> bytes:
    """A small sample of the picture: a real camera never gives two identical frames."""
    return frame[::24, ::24].tobytes()


class CameraSource:
    """Grabs frames continuously; ``latest()`` returns (frame, sequence number).

    ``source`` may be a camera index, a video file, or a folder of images (used
    for demonstrations and automated tests). ``opener`` replaces opening the device (tests).
    """

    def __init__(self, index: int = 0, backend: str = "auto", width: int = 1280, height: int = 720,
                 source: str = "", opener=None, clock=time.monotonic):
        self.index = index
        self.backend = backend
        self.width = width
        self.height = height
        self.source = source
        self._opener = opener or self._open
        self.clock = clock
        self._frame = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()  # the running capture thread's own stop signal
        self._thread: threading.Thread | None = None
        self.state = "stopped"  # stopped | opening | ok | error
        self.error = ""
        self.fps = 0.0
        self.brightness = 0.0
        self.wanted = False  # started and not stopped
        self.last_frame_at = 0.0  # clock() of the newest frame
        self.ever_worked = False  # delivered frames at some point since the program started
        self.restarts = 0
        self._opening_since = 0.0

    # ----------------------------------------------------------------- API
    def start(self) -> None:
        self.wanted = True
        if self._thread and self._thread.is_alive():
            return
        self._launch()

    def stop(self) -> None:
        self.wanted = False
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None
        self.state = "stopped"

    def restart(self, reason: str = "") -> None:
        """Close the camera and open it again (after the computer slept, or when frames stopped).
        A capture thread stuck inside the driver is left behind; it ends when the driver lets go."""
        if not self.wanted:
            return
        log.warning("camera: starting again (%s)", reason or "requested")
        self.restarts += 1
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        with self._lock:
            self._frame = None
        self.state, self.error = "opening", MSG_RESTART
        self._launch()

    def watch(self) -> str:
        """Start the camera again if it stopped delivering frames; returns why ("" if all is well)."""
        if not self.wanted or self.source.strip():
            return ""
        now = self.clock()
        if self._thread is None or not self._thread.is_alive():
            reason = "the capture thread ended"
        elif self.state == "ok" and now - self.last_frame_at > STALL_SECONDS:
            reason = "no new frames"
        elif self.state == "opening" and now - self._opening_since > OPEN_SECONDS:
            reason = "opening the camera hangs"
        else:
            return ""
        self.restart(reason)
        return reason

    def dead_for(self) -> float:
        """Seconds without a frame although the camera worked earlier (0 if it never did)."""
        if not (self.wanted and self.ever_worked) or self.source.strip():
            return 0.0
        return max(0.0, self.clock() - self.last_frame_at)

    def latest(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._frame, self._seq

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------- worker
    def _launch(self) -> None:
        self._stop = stop = threading.Event()
        self.last_frame_at = self._opening_since = self.clock()
        self._thread = threading.Thread(target=self._run, args=(stop,), name="camera", daemon=True)
        self._thread.start()

    def _publish(self, frame: np.ndarray, stop: threading.Event) -> None:
        if stop.is_set():  # a thread being replaced must not overwrite the new one's frames
            return
        with self._lock:
            self._frame = frame
            self._seq += 1
        self.last_frame_at = self.clock()
        self.ever_worked = True

    def _run(self, stop: threading.Event) -> None:
        src = self.source.strip()
        if src and Path(src).is_dir():
            self._run_folder(Path(src), stop)
        elif src and Path(src).is_file():
            self._run_video(src, stop)
        else:
            self._run_camera(stop)

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

    def _set(self, stop: threading.Event, state: str, error: str = "") -> None:
        if not stop.is_set():
            self.state, self.error = state, error

    def _run_camera(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._opening_since = self.clock()
            self._set(stop, "opening", self.error or MSG_OPENING)  # keep the reason while retrying
            try:
                cap, frame = self._opener()
            except Exception:
                log.exception("camera: opening failed")
                cap, frame = None, None
            if cap is None:
                self._set(stop, "error", MSG_CANNOT_OPEN)
                if stop.wait(2.0):
                    break
                continue
            if stop.is_set():
                cap.release()
                break
            self._set(stop, "ok")
            self._publish(frame, stop)
            why = self._read_until_broken(cap, frame, stop)
            try:
                cap.release()
            except Exception:
                log.exception("camera: release failed")
            if not stop.is_set():
                self._set(stop, "error", why)
                stop.wait(1.0)

    def _read_until_broken(self, cap, frame: np.ndarray, stop: threading.Event) -> str:
        """Publish frames until the camera fails; returns the message to show while reopening."""
        failures, count, t0 = 0, 0, self.clock()
        sig, same_since = _signature(frame), self.clock()
        try:
            while not stop.is_set():
                ok, frame = cap.read()
                if stop.is_set():
                    break
                if not ok or frame is None:
                    failures += 1
                    if failures > 30:
                        return MSG_LOST  # disconnected, or the handle died while the computer slept
                    time.sleep(0.02)
                    continue
                failures = 0
                now = self.clock()
                s = _signature(frame)
                if s == sig:
                    if now - same_since > FROZEN_SECONDS:
                        return MSG_FROZEN
                    continue  # the same picture again is not a new frame
                sig, same_since = s, now
                self._publish(frame, stop)
                count += 1
                if count % 15 == 0:
                    self.fps = 15 / max(now - t0, 1e-6)
                    t0 = now
                    self.brightness = float(cv2.cvtColor(frame[::8, ::8], cv2.COLOR_BGR2GRAY).mean())
        except Exception:
            log.exception("camera: reading failed")
            return MSG_LOST
        return ""

    def _run_video(self, path: str, stop: threading.Event) -> None:
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        self.state = "ok"
        while not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self._publish(frame, stop)
            stop.wait(1.0 / fps)
        cap.release()

    def _run_folder(self, folder: Path, stop: threading.Event) -> None:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXT)
        if not files:
            self.state, self.error = "error", "لا توجد صور في المجلد المحدد."
            return
        self.state = "ok"
        i = 0
        while not stop.is_set():
            img = cv2.imread(str(files[(i // 10) % len(files)]))
            if img is not None:
                self._publish(img, stop)
            i += 1
            stop.wait(0.1)
