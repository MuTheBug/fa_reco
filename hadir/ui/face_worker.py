"""Background thread that owns the face engine.

Modes: ``idle``, ``recognize`` (kiosk), ``enroll`` (guided capture) and
``diagnose`` (recognition test screen).  Only this thread touches the neural
networks; the GUI thread exchanges data with it through Qt signals.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback

import numpy as np

from PySide6.QtCore import QThread, Signal

from ..face.enroll import EnrollmentSession
from ..face.pipeline import FaceEngine, RecognitionSession
from ..face.service import ensure_embeddings, load_galleries


class FaceWorker(QThread):
    ready = Signal(str)  # "" when loaded, otherwise an error message
    progress = Signal(int, int)
    status = Signal(object)
    decision = Signal(object, object)  # Decision, frame
    enroll_status = Signal(object)
    analysis = Signal(object, object)  # Analysis, frame

    def __init__(self, db, camera, settings: dict, parent=None):
        super().__init__(parent)
        self.db = db
        self.camera = camera
        self.settings = dict(settings)
        self.engine: FaceEngine | None = None
        self._lock = threading.Lock()
        self._mode = "idle"
        self._pending: tuple | None = None
        self._stop = False
        self._galleries = None
        self.session: RecognitionSession | None = None
        self.enrollment: EnrollmentSession | None = None
        self.load_error = ""
        self.probe_flip = False

    # ------------------------------------------------------------ GUI side
    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._pending = (mode,)

    @property
    def mode(self) -> str:
        return self._mode

    def begin_enrollment(self) -> EnrollmentSession:
        """Create the enrollment session here so the caller holds the right object."""
        s = self.settings
        sess = EnrollmentSession(self.engine, mirror=bool(s.get("camera.mirror", True)),
                                 min_eye_px=float(s.get("recog.min_eye_px", 40)))
        with self._lock:
            self.enrollment = sess
            self._pending = ("enroll",)
        return sess

    def pause(self, timeout: float = 3.0) -> str:
        """Switch to idle and wait until the worker stops using the networks.
        Returns the previous mode so the caller can resume it."""
        previous = self._mode
        self.set_mode("idle")
        end = time.monotonic() + timeout
        while self.isRunning() and self._mode != "idle" and time.monotonic() < end:
            time.sleep(0.02)
        return previous

    def configure(self, settings: dict) -> None:
        with self._lock:
            self.settings.update(settings)
            self._pending = self._pending or (self._mode,)

    def reload_galleries(self) -> None:
        """Re-read templates from the database (call after enrollment or edits)."""
        if self.engine is None:
            return
        gal = load_galleries(self.db, self.engine)
        with self._lock:
            self._galleries = gal

    def stop(self) -> None:
        self._stop = True
        self.wait(5000)

    @property
    def ready_ok(self) -> bool:
        return self.engine is not None

    # --------------------------------------------------------- worker side
    def run(self) -> None:
        try:
            self.engine = FaceEngine(self.settings.get("recog.engine", "dual"))
            ensure_embeddings(self.db, self.engine, lambda i, n: self.progress.emit(i, n))
            self.engine.set_galleries(load_galleries(self.db, self.engine))
            self.probe_flip = self._fast_enough_for_flip()
        except Exception as exc:  # models missing / corrupted
            self.load_error = f"{exc}"
            traceback.print_exc()
            self.ready.emit(self.load_error or "تعذّر تحميل نظام التعرّف")
            return
        self.ready.emit("")
        last_seq = -1
        while not self._stop:
            with self._lock:
                pending, self._pending = self._pending, None
                gal, self._galleries = self._galleries, None
            if gal is not None:
                self.engine.set_galleries(gal)
            if pending:
                self._switch(pending[0])
            if self._mode == "idle":
                time.sleep(0.05)
                continue
            frame, seq = self.camera.latest()
            if frame is None or seq == last_seq:
                time.sleep(0.01)
                continue
            last_seq = seq
            try:
                if self._mode == "recognize":
                    st, dec = self.session.process(frame)
                    self.status.emit(st)
                    if dec is not None:
                        self.decision.emit(dec, frame)
                elif self._mode == "enroll":
                    self.enroll_status.emit(self.enrollment.feed(frame))
                elif self._mode == "diagnose":
                    a = self.engine.analyze(frame, min_eye_px=self.settings.get("recog.min_eye_px", 40))
                    self.analysis.emit(a, frame)
            except Exception:
                traceback.print_exc()
                time.sleep(0.2)

    def _fast_enough_for_flip(self) -> bool:
        """Averaging each probe with its mirror image adds accuracy but doubles the cost;
        enable it only when the computer keeps recognition well under a second."""
        chip = np.random.default_rng(0).integers(0, 255, (112, 112, 3), dtype=np.uint8)
        self.engine.primary.embed(chip, flip=False)  # warm-up
        t0 = time.perf_counter()
        for _ in range(2):
            self.engine.primary.embed(chip, flip=False)
        per_frame = (time.perf_counter() - t0) / 2
        logging.getLogger("hadir").info("primary embedder %.0f ms per frame", per_frame * 1000)
        return per_frame < 0.11

    def _switch(self, mode: str) -> None:
        s = self.settings
        mirror = bool(s.get("camera.mirror", True))
        if mode == "recognize":
            self.session = RecognitionSession(
                self.engine, security=s.get("recog.security", "high"),
                liveness=s.get("recog.liveness", "auto"), mirror=mirror,
                min_eye_px=float(s.get("recog.min_eye_px", 40)), probe_flip=self.probe_flip)
        elif mode == "enroll" and self.enrollment is None:
            self.enrollment = EnrollmentSession(self.engine, mirror=mirror,
                                                min_eye_px=float(s.get("recog.min_eye_px", 40)))
        self._mode = mode
