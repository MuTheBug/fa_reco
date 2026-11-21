from datetime import datetime

import numpy as np
import pytest

from hadir.face import models
from hadir.face.liveness import HeadTurnChallenge

pytestmark = pytest.mark.skipif(
    not (models.is_available(models.YUNET) and models.is_available(models.ARCFACE_R100)
         and models.is_available(models.SFACE)),
    reason="face models not installed (run tools/fetch_models.py)")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def tick(self, dt=0.15):
        self.t += dt


@pytest.fixture(scope="module")
def engine():
    from hadir.face.pipeline import FaceEngine

    return FaceEngine("dual")


@pytest.fixture(scope="module")
def enrolled(engine, tmp_path_factory):
    """DB with people A, B, C, D enrolled from two photos each (via add_photo)."""
    from hadir.db.seed import seed_defaults
    from hadir.db.store import Database
    from hadir.face.enroll import EnrollmentSession
    from hadir.face.service import load_galleries, save_captures

    from .facedata import PEOPLE, load

    db = Database(tmp_path_factory.mktemp("db") / "f.db", clock=lambda: datetime(2026, 10, 28, 9))
    seed_defaults(db)
    ids = {}
    for i, person in enumerate("ABCD"):
        eid = db.employees.save(dict(code=str(100 + i), full_name=f"موظف {person}"))
        ids[person] = eid
        sess = EnrollmentSession(engine)
        for f in PEOPLE[person][:2]:
            assert sess.add_photo(load(f)) is None
        save_captures(db, eid, sess.final_samples())
    engine.set_galleries(load_galleries(db, engine))
    return db, ids


def run(engine, frames, liveness="off", security="high"):
    from hadir.face.pipeline import RecognitionSession

    clock = Clock()
    sess = RecognitionSession(engine, security=security, liveness=liveness, clock=clock)
    for f in frames:
        st, decision = sess.process(f)
        clock.tick()
        if decision:
            return decision, st
    return None, st


def test_known_people_accepted(engine, enrolled):
    from .facedata import PEOPLE, load, webcam_frames

    db, ids = enrolled
    for person, probe in (("A", PEOPLE["A"][3]), ("B", PEOPLE["B"][2]), ("C", PEOPLE["C"][1])):
        frames = webcam_frames(load(probe), 8, detector=engine.detector, seed=len(probe))
        d, _ = run(engine, frames)
        assert d is not None and d.kind == "accept", (person, d)
        assert d.employee_id == ids[person]
        assert d.score >= 0.45 and d.margin >= 0.10 and d.secondary >= 0.25


def test_stranger_rejected(engine, enrolled):
    from .facedata import PEOPLE, load, webcam_frames

    for probe in (PEOPLE["G"][0], PEOPLE["H"][0], PEOPLE["I"][0]):
        frames = webcam_frames(load(probe), 50, detector=engine.detector)
        d, _ = run(engine, frames)
        assert d is not None and d.kind == "unknown", (probe, d)
        assert d.score < 0.45


def test_low_quality_frames_give_guidance(engine, enrolled):
    from .facedata import PEOPLE, load, webcam_frames

    frames = webcam_frames(load(PEOPLE["A"][3]), 3, eye_px=18, detector=engine.detector)
    d, st = run(engine, frames)
    assert d is None and st.message == "اقترب من الكاميرا قليلاً"


def test_duplicate_enrollment_detected(engine, enrolled):
    from hadir.face.enroll import EnrollmentSession
    from hadir.face.service import duplicate_candidates

    from .facedata import PEOPLE, load

    db, ids = enrolled
    sess = EnrollmentSession(engine)
    sess.add_photo(load(PEOPLE["A"][4]))
    dups = duplicate_candidates(engine, sess.captures, exclude=None, threshold=0.5)
    assert dups and dups[0][0] == ids["A"]
    assert not duplicate_candidates(engine, sess.captures, exclude=ids["A"], threshold=0.5)


def test_gallery_cross_report_separates_people(engine, enrolled):
    report = engine.galleries[engine.primary.id].cross_report()
    assert len(report) == 4
    assert max(s for _, _, s in report) < 0.40


def test_printed_photo_attack_is_refused(engine, tmp_path):
    """A printed photo held to the camera: the passive check is unsure, and the photo cannot
    turn its head: refused as an attempt."""
    from hadir.db.store import Database
    from hadir.face.enroll import EnrollmentSession
    from hadir.face.gallery import Gallery
    from hadir.face.service import load_galleries, save_captures

    from .facedata import SILENT, load

    db = Database(tmp_path / "s.db")
    eid = db.employees.save(dict(code="1", full_name="ضحية"))
    attack = load("image_F1.jpg", SILENT)
    sess = EnrollmentSession(engine)
    assert sess.add_photo(attack) is None
    save_captures(db, eid, sess.captures)
    saved = engine.galleries
    try:
        engine.set_galleries(load_galleries(db, engine))
        rng = np.random.default_rng(1)
        frames = [np.clip(attack.astype(np.float32) + rng.normal(0, 3, attack.shape), 0, 255)
                  .astype(np.uint8) for _ in range(80)]
        d, _ = run(engine, frames, liveness="auto")
        assert d is not None and d.kind == "spoof" and d.employee_id == eid
        assert d.challenge in ("failed", "fake") and d.liveness < 0.25 and d.best is not None
        # The same frames are accepted when liveness checking is switched off.
        d2, _ = run(engine, frames[:10], liveness="off")
        assert d2.kind == "accept" and d2.employee_id == eid
    finally:
        engine.set_galleries(saved)
    assert isinstance(saved, dict) and Gallery


def test_head_turn_challenge_logic():
    ch = HeadTurnChallenge("left", mirror=True, started=0.0)
    for t, yaw in enumerate([0.01, 0.0, 0.05, 0.10, 0.16, 0.18]):
        res = ch.update(yaw, t * 0.2)
    assert res == "passed"
    # A photo rotated in front of the camera keeps the nose centred: never passes.
    ch = HeadTurnChallenge("right", mirror=True, started=0.0)
    for i in range(40):
        res = ch.update(0.02 * np.sin(i), i * 0.2)
    assert res == "failed"
    # Turning the wrong way does not count.
    ch = HeadTurnChallenge("right", mirror=True, started=0.0)
    for i, yaw in enumerate([0, 0, 0.2, 0.25, 0.3]):
        res = ch.update(yaw, i * 0.2)
    assert res == "pending"


def test_guided_enrollment_flow(engine, tmp_path):
    """Front captures come from several photos; turn/tilt steps are skipped (a still photo
    cannot turn), then the samples are saved and the person is recognised."""
    from hadir.db.store import Database
    from hadir.face.enroll import STEPS, EnrollmentSession
    from hadir.face.service import load_galleries, save_captures

    from .facedata import PEOPLE, load, webcam_frames

    clock = Clock()
    # simulated frames are photos, which the live-face check would (rightly) refuse
    sess = EnrollmentSession(engine, clock=clock, check_liveness=False)
    photos = [load(f) for f in PEOPLE["A"][:7]]
    frames = []
    for i, img in enumerate(photos):
        frames += webcam_frames(img, 2, eye_px=95, detector=engine.detector, seed=i)
    captured = 0
    for f in frames:
        if sess.done:
            break
        while not sess.done and STEPS[sess.step][0] != "front":
            sess.skip_step()
        st = sess.feed(f)
        clock.tick(0.5)
        captured = st.captured
    while not sess.done and STEPS[sess.step][0] != "front":
        sess.skip_step()
    assert captured >= 5
    samples = sess.final_samples()
    assert len(samples) >= 5 and all(c.pose == "front" for c in samples)
    db = Database(tmp_path / "e.db")
    eid = db.employees.save(dict(code="9", full_name="مسجل بالكاميرا"))
    save_captures(db, eid, samples)
    assert db.employees.photo(eid)
    saved = engine.galleries
    try:
        engine.set_galleries(load_galleries(db, engine))
        d, _ = run(engine, webcam_frames(load(PEOPLE["A"][7]), 8, detector=engine.detector, seed=99))
        assert d.kind == "accept" and d.employee_id == eid
    finally:
        engine.set_galleries(saved)


def test_enrollment_rejects_a_different_person(engine):
    from hadir.face.enroll import EnrollmentSession

    from .facedata import PEOPLE, load, webcam_frames

    clock = Clock()
    sess = EnrollmentSession(engine, clock=clock, check_liveness=False)
    for f in webcam_frames(load(PEOPLE["A"][0]), 2, eye_px=95, detector=engine.detector):
        sess.feed(f)
        clock.tick(0.5)
    n = len(sess.captures)
    st = None
    for f in webcam_frames(load(PEOPLE["B"][2]), 3, eye_px=95, detector=engine.detector):
        st = sess.feed(f)
        clock.tick(0.5)
    assert len(sess.captures) == n and st.tone == "bad"


def test_enrollment_refuses_a_photo_by_default(engine):
    from hadir.face.enroll import EnrollmentSession

    from .facedata import SILENT, load

    clock = Clock()
    sess = EnrollmentSession(engine, clock=clock)
    attack = load("image_F2.jpg", SILENT)
    st = None
    for _ in range(3):
        st = sess.feed(attack)
        clock.tick(0.5)
    assert not sess.captures and st.message == "يبدو أنها صورة وليست وجهاً حقيقياً"


def test_challenge_requires_same_identity(engine, enrolled, monkeypatch):
    """Recognised as A with a weak liveness score, then another face turns its head:
    the challenge must not pass for A."""
    from hadir.face.pipeline import RecognitionSession

    from .facedata import PEOPLE, load, webcam_frames

    # Photos pasted on a plain background look like held-up photos to the passive models;
    # here they stand for live but unsure faces.
    monkeypatch.setattr(engine.liveness, "score", lambda image, face: 0.5)
    db, ids = enrolled
    clock = Clock()
    sess = RecognitionSession(engine, liveness="always", clock=clock)
    decision = None
    for f in webcam_frames(load(PEOPLE["A"][3]), 6, detector=engine.detector):
        st, decision = sess.process(f)
        clock.tick()
        if sess.state == "challenge" or decision:
            break
    assert sess.state == "challenge" and decision is None
    for f in webcam_frames(load(PEOPLE["G"][0]), 6, detector=engine.detector, seed=3):
        st, decision = sess.process(f)
        clock.tick()
        if decision:
            break
    assert decision is not None and decision.kind != "accept"


def test_a_video_on_a_phone_is_refused_without_a_head_turn_test(engine, tmp_path):
    """A colleague's video on a phone held to the camera (it can turn its head, so a head-turn
    test would not stop it): the screen is recognised as fake and the attempt is refused at once,
    naming whom the face on the screen resembles."""
    from hadir.db.store import Database
    from hadir.face.enroll import EnrollmentSession
    from hadir.face.service import load_galleries, save_captures

    from .facedata import SILENT, load

    db = Database(tmp_path / "v.db")
    eid = db.employees.save(dict(code="7", full_name="زميل غائب"))
    replay = load("image_F2.jpg", SILENT)  # a face replayed on a phone screen
    sess = EnrollmentSession(engine)
    assert sess.add_photo(replay) is None
    save_captures(db, eid, sess.captures)
    saved = engine.galleries
    try:
        engine.set_galleries(load_galleries(db, engine))
        rng = np.random.default_rng(2)
        frames = [np.clip(replay.astype(np.float32) + rng.normal(0, 3, replay.shape), 0, 255)
                  .astype(np.uint8) for _ in range(20)]
        for mode in ("auto", "always"):
            d, _ = run(engine, frames, liveness=mode)
            assert d is not None and d.kind == "spoof" and d.employee_id == eid, mode
            assert d.challenge is None and d.liveness < 0.1  # no head-turn test was asked for
    finally:
        engine.set_galleries(saved)


class Scripted:
    """An engine whose frames are given: a recognised face with a passive liveness score and a
    head direction per frame (to test the head-turn test without real video)."""

    def __init__(self, frames, emp_id=7):
        from types import SimpleNamespace

        self.frames = list(frames)
        self.emp_id = emp_id
        self.primary = SimpleNamespace(id="arcface")
        self.secondary = None

    def analyze(self, frame, *, live=True, **kw):
        from hadir.face.detector import Face
        from hadir.face.pipeline import Analysis
        from hadir.face.quality import Quality

        score, yaw = self.frames.pop(0)
        face = Face(np.array([540.0, 220, 200, 200]),
                    np.array([[600, 290], [680, 290], [640 + 80 * yaw, 330], [610, 370], [670, 370]], float), 0.95)
        q = Quality(eye_px=80, yaw=yaw, pitch=0.6, roll=0, sharpness=200, brightness=120, contrast=50,
                    det_score=0.95)
        return Analysis(faces=[face], face=face, quality=q, chip=np.zeros((112, 112, 3), np.uint8),
                        scores={"arcface": {self.emp_id: 0.82, 8: 0.12}}, liveness=score if live else None)


def sweep(n):
    """A head turning from side to side, as in a recorded video."""
    return [0.3 * np.sin(i * 0.7) for i in range(n)]


def run_scripted(lives, yaws, liveness="auto"):
    from hadir.face.pipeline import RecognitionSession

    clock = Clock()
    engine = Scripted(zip(lives, yaws))
    sess = RecognitionSession(engine, liveness=liveness, clock=clock)
    states, decision = [], None
    while engine.frames and decision is None:
        st, decision = sess.process(None)
        states.append(sess.state)
        clock.tick(0.2)
    return decision, states


def test_a_video_that_turns_its_head_during_the_test_is_caught():
    """The passive check is unsure, so the head-turn test is asked for; the video on the phone
    turns its head from side to side and so matches it, but the face does not look live while
    turning: refused as a video, never accepted."""
    n = 60
    for scan, turning in ((0.3, 0.02), (0.2, 0.15)):  # clearly a screen / never live
        d, states = run_scripted([scan] * 2 + [turning] * (n - 2), [0.0] * 4 + sweep(n - 4))
        assert "challenge" in states, (scan, turning)
        assert d is not None and d.kind == "spoof" and d.employee_id == 7 and d.challenge == "fake"


def test_a_real_face_with_an_unsure_score_passes_the_head_turn_test():
    """A real face the passive check was unsure about (a blurred first look, poor light) turns
    its head as asked and looks live doing it: accepted."""
    for scan, turning in ((0.5, 0.5), (0.2, 0.8), (0.6, 0.3)):
        n = 30  # the direction asked for is random: the head turns both ways within the time
        d, states = run_scripted([scan] * 2 + [turning] * (n - 2), [0.0] * 4 + sweep(n - 4))
        assert "challenge" in states
        assert d is not None and d.kind == "accept" and d.employee_id == 7 and d.challenge == "passed"


def test_a_clearly_fake_face_is_not_offered_the_head_turn_test():
    d, states = run_scripted([0.05] * 10, sweep(10))
    assert "challenge" not in states
    assert d is not None and d.kind == "spoof" and d.employee_id == 7 and d.challenge is None
    d, _ = run_scripted([0.05] * 10, sweep(10), liveness="off")
    assert d.kind == "accept"
