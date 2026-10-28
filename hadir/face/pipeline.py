"""Frame analysis and the multi-frame recognition decision.

A punch is accepted only when *all* of the following hold:

1. The face passes quality gates (size, light, sharpness, pose).
2. Over several good frames the averaged ArcFace similarity to one employee is
   above the security threshold, and beats the runner-up by a clear margin
   (protects look-alikes such as brothers).
3. The independent SFace model does not prefer somebody else and agrees that
   the face resembles the claimed employee (dual-engine verification).
4. The passive anti-spoofing models say the face is live, otherwise the person
   must pass a random head-turn challenge while staying the same identity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..core.settings import SECURITY_LEVELS
from .align import align_face
from .detector import Face, FaceDetector
from .embedder import Embedder
from .gallery import Gallery
from .liveness import HeadTurnChallenge, PassiveLiveness
from .models import ARCFACE_R100, SFACE, is_available
from .quality import Quality, assess

LIVE_PASS = 0.70  # passive score that needs no challenge
LIVE_SPOOF = 0.25  # passive score treated as a likely attack
SECONDARY_FLOOR = 0.25  # SFace must at least find a weak resemblance
SCAN_TIMEOUT = 5.0
LOST_FACE = 0.8


@dataclass
class Analysis:
    faces: list = field(default_factory=list)
    face: Face | None = None
    quality: Quality | None = None
    chip: np.ndarray | None = None
    emb: dict = field(default_factory=dict)  # model id -> embedding
    scores: dict = field(default_factory=dict)  # model id -> {employee id: score}
    liveness: float | None = None
    crowded: bool = False
    ms: float = 0.0


class FaceEngine:
    """Owns the networks; analyses single frames.  Not thread-safe: use from one thread."""

    def __init__(self, mode: str = "dual", threads: int | None = None):
        self.detector = FaceDetector()
        have_arc, have_sf = is_available(ARCFACE_R100), is_available(SFACE)
        if not (have_arc or have_sf):
            raise RuntimeError("لم يُعثر على نماذج التعرّف على الوجه في مجلد models.")
        primary = ARCFACE_R100 if have_arc and mode != "sface" else SFACE
        secondary = SFACE if (mode == "dual" and have_arc and have_sf) else None
        self.primary = Embedder(primary, threads)
        self.secondary = Embedder(secondary, threads) if secondary else None
        self.liveness = PassiveLiveness()
        self.galleries: dict[str, Gallery] = {}

    @property
    def embedders(self) -> list[Embedder]:
        return [e for e in (self.primary, self.secondary) if e is not None]

    def set_galleries(self, galleries: dict[str, Gallery]) -> None:
        self.galleries = galleries

    def gallery_size(self) -> int:
        g = self.galleries.get(self.primary.id)
        return 0 if g is None else len(g)

    @staticmethod
    def pick_face(faces: list[Face], shape) -> tuple[Face | None, bool]:
        """Largest face, preferring the centre when two are similar in size."""
        if not faces:
            return None, False
        h, w = shape[:2]
        big = faces[0].area
        near = [f for f in faces if f.area >= 0.55 * big]
        centre = np.array([w / 2, h / 2])
        best = min(near, key=lambda f: np.linalg.norm(f.center - centre) / max(w, h) - f.area / big)
        return best, len(near) > 1

    def embed_all(self, chip: np.ndarray, flip: bool = True) -> dict[str, np.ndarray]:
        return {e.id: e.embed(chip, flip=flip)[0] for e in self.embedders}

    def analyze(self, image: np.ndarray, *, min_eye_px: float = 40, embed: bool = True,
                live: bool = True, strict: bool = False, flip: bool = False,
                check_pose: bool = True) -> Analysis:
        t0 = time.perf_counter()
        a = Analysis()
        a.faces = self.detector.detect(image)
        a.face, a.crowded = self.pick_face(a.faces, image.shape)
        if a.face is None:
            a.ms = (time.perf_counter() - t0) * 1000
            return a
        a.chip = align_face(image, a.face.landmarks)
        a.quality = assess(a.face, a.chip, min_eye_px, strict, check_pose)
        if a.quality.ok and embed:
            for e in self.embedders:
                vec = e.embed(a.chip, flip=flip)[0]
                a.emb[e.id] = vec
                g = self.galleries.get(e.id)
                if g is not None and not g.empty:
                    a.scores[e.id] = g.person_scores(vec)
        if a.quality.ok and live and self.liveness.available:
            a.liveness = self.liveness.score(image, a.face)
        a.ms = (time.perf_counter() - t0) * 1000
        return a


@dataclass
class Evidence:
    t: float
    primary: dict
    secondary: dict | None
    liveness: float | None
    quality: Quality
    face: Face
    chip: np.ndarray
    emb: dict
    frame: np.ndarray


@dataclass
class Decision:
    kind: str  # accept | unknown | ambiguous | spoof
    employee_id: int | None = None
    score: float = 0.0
    margin: float = 0.0
    secondary: float | None = None
    liveness: float | None = None
    challenge: str | None = None
    runner_up: int | None = None
    best: Evidence | None = None


@dataclass
class Status:
    state: str = "idle"  # idle | scanning | challenge | cooldown
    message: str = "قف أمام الكاميرا"
    tone: str = "idle"  # idle | busy | ok | bad | warn
    box: np.ndarray | None = None
    landmarks: np.ndarray | None = None
    progress: float = 0.0
    challenge: str | None = None
    candidates: list = field(default_factory=list)
    quality: Quality | None = None
    liveness: float | None = None
    ms: float = 0.0


class RecognitionSession:
    """Turns a stream of frames into accept / reject decisions."""

    def __init__(self, engine: FaceEngine, security: str = "high", liveness: str = "auto",
                 mirror: bool = True, min_eye_px: float = 40, clock=time.monotonic,
                 probe_flip: bool = False):
        self.engine = engine
        self.params = dict(SECURITY_LEVELS.get(security, SECURITY_LEVELS["high"]))
        self.liveness_mode = liveness
        self.mirror = mirror
        self.min_eye_px = min_eye_px
        self.clock = clock
        self.probe_flip = probe_flip
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        self.state = "idle"
        self.evidence: list[Evidence] = []
        self.started = 0.0
        self.last_seen = 0.0
        self.last_center = None
        self.challenge: HeadTurnChallenge | None = None
        self.claim: Decision | None = None
        self.cooldown_until = 0.0
        self.cooldown_kind = ""

    @property
    def thresholds(self) -> tuple[float, float]:
        if self.engine.primary.id == SFACE.id:
            return self.params["sface"], self.params["sface_margin"]
        return self.params["arc"], self.params["margin"]

    def _same_track(self, face: Face) -> bool:
        if self.last_center is None:
            return True
        jump = np.linalg.norm(face.center - self.last_center)
        return jump < 0.6 * max(face.box[2], 1)

    # --------------------------------------------------------------- process
    def process(self, frame: np.ndarray) -> tuple[Status, Decision | None]:
        now = self.clock()
        scanning = self.state in ("idle", "scanning", "challenge")
        a = self.engine.analyze(frame, min_eye_px=self.min_eye_px, embed=scanning,
                                live=self.state in ("idle", "scanning"), flip=self.probe_flip)
        st = Status(state=self.state, quality=a.quality, liveness=a.liveness, ms=a.ms)
        if a.face is not None:
            st.box, st.landmarks = a.face.box, a.face.landmarks

        if a.face is None:
            if self.state != "idle" and now - self.last_seen > LOST_FACE:
                self.reset()
            st.state = self.state
            if self.state == "cooldown":
                st.tone, st.message = "ok" if self.cooldown_kind == "accept" else "bad", ""
            return st, None

        same = self._same_track(a.face)
        self.last_seen = now
        self.last_center = a.face.center

        if self.state == "cooldown":
            if (not same or (now >= self.cooldown_until and self.cooldown_kind != "accept")
                    or now >= self.cooldown_until + 12):  # stood still a long time: retry
                self._start(now)
                st.state, st.tone, st.message = "scanning", "busy", "جارٍ التحقق…"
                return st, None  # this frame was analysed without embeddings
            st.state, st.tone, st.message = "cooldown", (
                "ok" if self.cooldown_kind == "accept" else "bad"), ""
            return st, None

        if self.state == "idle" or not same:
            self._start(now)
        st.state = self.state

        if a.crowded:
            st.tone, st.message = "warn", "شخص واحد فقط أمام الكاميرا من فضلك"
            return st, None
        if not a.quality.ok:
            st.tone, st.message = "warn", a.quality.reason
            if self.state == "challenge" and self.challenge:
                # Unusable frames never count towards the head turn (only the timeout runs).
                self.challenge.update(None, now)
                return self._challenge_status(st, a, now)
            return st, None
        if self.engine.primary.id not in a.scores:
            st.tone, st.message = "bad", "لا توجد بصمات وجه مسجلة بعد"
            return st, None

        if self.state == "challenge":
            return self._challenge_step(st, a, frame, now)
        return self._scan_step(st, a, frame, now)

    def _start(self, now: float):
        self.state = "scanning"
        self.evidence = []
        self.started = now
        self.challenge = None
        self.claim = None

    # --------------------------------------------------------------- scanning
    def _scan_step(self, st: Status, a: Analysis, frame, now: float):
        pid = self.engine.primary.id
        sid = self.engine.secondary.id if self.engine.secondary else None
        self.evidence.append(Evidence(now, a.scores[pid], a.scores.get(sid) if sid else None,
                                      a.liveness, a.quality, a.face, a.chip, a.emb, frame))
        self.evidence = self.evidence[-6:]
        need = self.params["frames"]
        st.candidates = self._ranked(self.evidence)[:3]
        st.progress = min(1.0, len(self.evidence) / need)
        st.tone, st.message = "busy", "جارٍ التحقق… ثبّت نظرك على الكاميرا"
        if len(self.evidence) < need:
            return st, None

        recent = self.evidence[-max(need, 3):]
        ranked = self._ranked(recent)
        best_id, best = ranked[0]
        second_id, second = ranked[1] if len(ranked) > 1 else (None, -1.0)
        thr, margin = self.thresholds
        timed_out = now - self.started > SCAN_TIMEOUT
        if best >= thr and best - second >= margin:
            sec_ok, sec_score = self._secondary_agrees(recent, best_id)
            if not sec_ok:
                if timed_out:
                    return self._finish(st, now, Decision("ambiguous", best_id, best, best - second,
                                                          sec_score, runner_up=second_id,
                                                          best=self._best(recent)))
                return st, None
            live = [e.liveness for e in recent if e.liveness is not None]
            live_score = float(np.mean(live)) if live else None
            claim = Decision("accept", best_id, best, best - second, sec_score, live_score,
                             runner_up=second_id, best=self._best(recent))
            needs_challenge = self.liveness_mode == "always" or (
                self.liveness_mode == "auto" and live_score is not None and live_score < LIVE_PASS)
            if needs_challenge:
                self.state = "challenge"
                self.claim = claim
                self.challenge = HeadTurnChallenge.random(self.mirror, now)
                self.challenge.update(a.quality.yaw, now)
                return self._challenge_status(st, a, now)
            return self._finish(st, now, claim)
        clearly_unknown = len(self.evidence) >= 5 and best < thr - 0.15
        if timed_out or clearly_unknown:
            kind = "ambiguous" if best >= thr else "unknown"
            return self._finish(st, now, Decision(kind, best_id if kind == "ambiguous" else None,
                                                  best, best - second, runner_up=second_id,
                                                  best=self._best(recent)))
        return st, None

    @staticmethod
    def _ranked(evidence: list[Evidence]) -> list[tuple[int, float]]:
        ids = set()
        for e in evidence:
            ids.update(e.primary)
        means = {i: float(np.mean([e.primary.get(i, 0.0) for e in evidence])) for i in ids}
        return sorted(means.items(), key=lambda kv: kv[1], reverse=True) or [(None, 0.0)]

    @staticmethod
    def _best(evidence: list[Evidence]) -> Evidence:
        return max(evidence, key=lambda e: e.quality.score)

    def _secondary_agrees(self, evidence: list[Evidence], emp_id: int) -> tuple[bool, float | None]:
        if self.engine.secondary is None:
            return True, None
        sec = [e.secondary for e in evidence if e.secondary]
        if not sec:
            return True, None
        ids = set().union(*sec)
        means = {i: float(np.mean([s.get(i, 0.0) for s in sec])) for i in ids}
        mine = means.get(emp_id, 0.0)
        rival = max((v for k, v in means.items() if k != emp_id), default=-1.0)
        return (mine >= SECONDARY_FLOOR and mine >= rival - 0.02), mine

    # -------------------------------------------------------------- challenge
    def _challenge_step(self, st: Status, a: Analysis, frame, now: float):
        claim = self.claim
        score = a.scores[self.engine.primary.id].get(claim.employee_id, 0.0)
        # The same person must stay in front of the camera throughout the movement (stops
        # a photo being swapped for another face); a turned head is allowed a lower score.
        limit = self.thresholds[0] - (0.15 if abs(a.quality.yaw) < 0.25 else 0.25)
        if score < limit:
            return self._finish(st, now, Decision("unknown", None, score, best=claim.best))
        self.challenge.update(a.quality.yaw, now)
        return self._challenge_status(st, a, now)

    def _challenge_status(self, st: Status, a: Analysis, now: float):
        ch = self.challenge
        st.state, st.challenge = "challenge", ch.direction
        if ch.result == "passed":
            self.claim.challenge = "passed"
            return self._finish(st, now, self.claim)
        if ch.result == "failed":
            kind = "spoof" if (self.claim.liveness or 1) < LIVE_SPOOF else "unknown"
            d = Decision(kind, self.claim.employee_id if kind == "spoof" else None,
                         self.claim.score, liveness=self.claim.liveness, challenge="failed",
                         best=self.claim.best)
            return self._finish(st, now, d)
        st.tone, st.message = "warn", ch.prompt
        st.progress = min(1.0, (now - ch.started) / ch.timeout)
        return st, None

    def _finish(self, st: Status, now: float, decision: Decision):
        self.state = "cooldown"
        self.cooldown_kind = decision.kind
        self.cooldown_until = now + (2.5 if decision.kind == "accept" else 2.0)
        st.state = "cooldown"
        st.tone = "ok" if decision.kind == "accept" else "bad"
        st.message = ""
        st.progress = 1.0
        return st, decision
