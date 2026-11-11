"""The camera comes back after the laptop slept (fake capture devices, no real camera)."""

import threading
import time
from datetime import date, timedelta

import numpy as np
import pytest

from hadir.face import camera as C


class FakeCap:
    """A capture device: ``frames`` live frames, then ``after``: "same", "fail" or "hang"."""

    def __init__(self, frames=10**9, after="fail"):
        self.frames, self.after = frames, after
        self.n = 0
        self.released = False
        self.gate = threading.Event()  # "hang" waits on this forever (a driver stuck after sleep)

    def read(self):
        self.n += 1
        time.sleep(0.005)
        if self.n <= self.frames:
            return True, np.random.default_rng(self.n).integers(0, 255, (48, 64, 3), dtype=np.uint8)
        if self.after == "same":
            return True, np.zeros((48, 64, 3), np.uint8)
        if self.after == "hang":
            self.gate.wait()
        return False, None

    def release(self):
        self.released = True


class Opener:
    """Hands out the given devices one after another (then healthy ones)."""

    def __init__(self, *caps):
        self.caps = list(caps)
        self.opened = []

    def __call__(self):
        cap = self.caps.pop(0) if self.caps else FakeCap()
        if cap == "hang":  # opening itself never returns
            threading.Event().wait()
        self.opened.append(cap)
        return cap, np.full((48, 64, 3), len(self.opened), np.uint8)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(C, "FROZEN_SECONDS", 0.2)
    monkeypatch.setattr(C, "STALL_SECONDS", 0.4)
    monkeypatch.setattr(C, "OPEN_SECONDS", 0.4)


def frames_flow(cam):
    _, seq = cam.latest()
    return wait_for(lambda: cam.latest()[1] > seq + 3)


def test_a_frozen_or_black_picture_reopens_the_camera():
    opener = Opener(FakeCap(frames=5, after="same"))
    cam = C.CameraSource(opener=opener)
    cam.start()
    try:
        assert wait_for(lambda: len(opener.opened) == 2)
        assert opener.opened[0].released and frames_flow(cam)
    finally:
        cam.stop()


def test_failing_reads_reopen_the_camera():
    opener = Opener(FakeCap(frames=5, after="fail"))
    cam = C.CameraSource(opener=opener)
    cam.start()
    try:
        assert wait_for(lambda: len(opener.opened) == 2) and frames_flow(cam)
    finally:
        cam.stop()


def test_watch_replaces_a_capture_stuck_in_the_driver():
    stuck = FakeCap(frames=5, after="hang")
    opener = Opener(stuck)
    cam = C.CameraSource(opener=opener)
    cam.start()
    try:
        assert wait_for(lambda: stuck.n > 5)
        assert cam.watch() == ""  # not stalled long enough yet
        time.sleep(0.5)
        stale = cam.latest()[0]
        assert cam.watch() == "no new frames" and cam.restarts == 1
        assert cam.latest()[0] is not stale  # the frozen picture is not shown or recognised meanwhile
        assert wait_for(lambda: len(opener.opened) == 2) and frames_flow(cam)
        assert cam.watch() == ""
    finally:
        stuck.gate.set()
        cam.stop()


def test_watch_restarts_an_ended_thread_and_a_hanging_open():
    opener = Opener("hang")
    cam = C.CameraSource(opener=opener)
    cam.start()
    try:
        time.sleep(0.5)
        assert cam.state == "opening" and cam.watch() == "opening the camera hangs"
        assert frames_flow(cam)
        cam._stop.set()  # the capture thread ends although the camera is still wanted
        assert wait_for(lambda: not cam.running)
        assert cam.watch() == "the capture thread ended" and frames_flow(cam)
    finally:
        cam.stop()
    assert cam.watch() == "" and cam.dead_for() == 0.0  # stopped on purpose: left alone


def test_dead_for_counts_only_after_the_camera_worked():
    clock = [100.0]
    cam = C.CameraSource(opener=Opener(), clock=lambda: clock[0])
    cam.wanted = True
    assert cam.dead_for() == 0.0
    cam._publish(np.zeros((4, 4, 3), np.uint8), threading.Event())
    clock[0] += 50
    assert cam.dead_for() == 50
    cam.source = "videos/demo.mp4"
    assert cam.dead_for() == 0.0


# ---------------------------------------------------------------- the window watches the camera
class WatchedCamera:
    state, error, fps, brightness = "ok", "", 0.0, 0.0
    wanted = True

    def __init__(self):
        self.calls = []
        self.dead = 0.0

    def latest(self):
        return None, 0

    def start(self):
        pass

    def stop(self):
        self.calls.append("stop")

    def restart(self, reason=""):
        self.calls.append(("restart", reason))

    def watch(self):
        self.calls.append("watch")
        return ""

    def dead_for(self):
        return self.dead


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("HADIR_DATA_DIR", str(tmp_path))
    from hadir.demo import demo_database
    from hadir.ui.main_window import AppContext, MainWindow
    from hadir.ui.selfcheck import IdleFaceWorker

    db = demo_database(tmp_path / "w.db", date(2026, 10, 28), employees=3, days=2)
    win = MainWindow(AppContext(db, WatchedCamera(), IdleFaceWorker()), kiosk_first=True)
    yield win
    win._allow_close = True
    win.close()


def test_the_window_restarts_the_camera_when_the_laptop_wakes_up(window):
    cam = window.ctx.camera
    assert window._watch_camera() == "" and cam.calls == ["watch"]
    window._wall -= 600  # ten minutes without a timer tick: the computer was asleep
    assert window._watch_camera() == "resumed" and cam.calls[-1] == ("restart", "the computer woke up")


def test_the_program_restarts_itself_when_the_camera_stays_dead(window):
    cam, db = window.ctx.camera, window.ctx.db
    relaunched = []
    window.relaunch = lambda: relaunched.append(True)
    cam.dead = 60
    window._watch_camera()
    assert not relaunched  # give opening it again a chance first
    cam.dead = 200
    window.in_kiosk = False
    window._watch_camera()
    assert not relaunched  # never in the middle of administration work
    window.in_kiosk = True
    assert window._watch_camera() == "relaunched" and relaunched == [True] and "stop" in cam.calls
    assert db.scalar("SELECT COUNT(*) FROM security_events WHERE kind = 'camera'") == 1
    window._watch_camera()
    assert relaunched == [True]  # at most once in half an hour
    earlier = db.now() - timedelta(minutes=31)
    db.settings.set("camera.last_relaunch", earlier.isoformat(timespec="seconds"), audit=False)
    window._watch_camera()
    assert relaunched == [True, True]
