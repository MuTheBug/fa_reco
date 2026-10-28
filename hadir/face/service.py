"""Glue between the face engine and the database."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from ..core.timeutil import parse_dt
from ..db.store import Database
from .enroll import Capture, chip_from_png, chip_png
from .gallery import Gallery
from .pipeline import Decision, FaceEngine

ADAPT_MAX = 8  # adaptive samples kept per employee
ADAPT_EVERY = timedelta(days=3)
DUPLICATE_MARGIN = 0.05


def ensure_embeddings(db: Database, engine: FaceEngine, progress=None) -> int:
    """Compute missing embeddings (e.g. after switching models) from stored face chips."""
    done = 0
    for e in engine.embedders:
        rows = db.faces.missing(e.id)
        for i, r in enumerate(rows):
            chip = chip_from_png(r["chip"])
            if chip is None:
                continue
            db.faces.set_embedding(r["id"], e.id, e.embed(chip, flip=True)[0])
            done += 1
            if progress:
                progress(i + 1, len(rows))
    return done


def load_galleries(db: Database, engine: FaceEngine) -> dict[str, Gallery]:
    out = {}
    for e in engine.embedders:
        sids, eids, mat = db.faces.gallery(e.id, e.dim)
        out[e.id] = Gallery(e.id, sids, eids, mat)
    return out


def duplicate_candidates(engine: FaceEngine, captures: list[Capture], exclude: int | None,
                         threshold: float) -> list[tuple[int, float]]:
    """Other employees whose templates resemble the new samples (possible double enrollment)."""
    g = engine.galleries.get(engine.primary.id)
    if g is None or g.empty or not captures:
        return []
    best: dict[int, float] = {}
    for c in captures:
        for emp, s in g.person_scores(c.emb[engine.primary.id]).items():
            if emp != exclude:
                best[emp] = max(best.get(emp, -1.0), s)
    return sorted(((e, s) for e, s in best.items() if s >= threshold), key=lambda x: -x[1])


def save_captures(db: Database, employee_id: int, captures: list[Capture], source: str = "enroll",
                  photo_from_best: bool = True) -> int:
    with db.tx():
        for c in captures:
            db.faces.add_sample(employee_id, chip_png(c.chip), c.quality, c.pose, source, c.emb)
        if photo_from_best and captures and not db.employees.photo(employee_id):
            best = max((c for c in captures if c.pose in ("front", "photo")), default=captures[0],
                       key=lambda c: c.quality)
            ok, buf = cv2.imencode(".jpg", best.chip, [cv2.IMWRITE_JPEG_QUALITY, 92])
            db.employees.set_photo(employee_id, buf.tobytes())
        db.audit("تسجيل بصمة الوجه", "employee", employee_id,
                 {"samples": len(captures), "source": source})
    return len(captures)


def maybe_adapt(db: Database, engine: FaceEngine, decision: Decision, threshold: float) -> bool:
    """Add a fresh template after a very confident match so the system follows slow changes
    (beard, glasses, ageing).  Conservative on purpose."""
    ev = decision.best
    if decision.kind != "accept" or ev is None or decision.employee_id is None:
        return False
    if decision.score < threshold + 0.12 or decision.margin < 0.2:
        return False
    if (decision.liveness or 0) < 0.9 and decision.challenge != "passed":
        return False
    if ev.quality.score < 0.75:
        return False
    g = engine.galleries.get(engine.primary.id)
    mine = g.templates_of(decision.employee_id) if g else np.zeros((0,))
    vec = ev.emb.get(engine.primary.id)
    if vec is None or len(mine) == 0:
        return False
    if float((mine @ vec).max()) > 0.80:
        return False  # nothing new to learn
    last = db.faces.last_adaptive_at(decision.employee_id)
    if last and db.now() - parse_dt(last) < ADAPT_EVERY:
        return False
    emb = engine.embed_all(ev.chip, flip=True)
    with db.tx():
        while db.faces.count(decision.employee_id, "adapt") >= ADAPT_MAX:
            db.faces.delete_sample(db.faces.oldest_adaptive(decision.employee_id)["id"])
        db.faces.add_sample(decision.employee_id, chip_png(ev.chip), ev.quality.score, "auto",
                            "adapt", emb)
    return True


def save_snapshot(root: Path, frame: np.ndarray, box, ts: datetime, tag: str) -> str:
    """Store a small evidence picture of the punch; returns the path relative to ``root``."""
    h, w = frame.shape[:2]
    x, y, bw, bh = [float(v) for v in box]
    cx, cy, s = x + bw / 2, y + bh / 2, max(bw, bh) * 1.7
    x0, y0 = int(max(0, cx - s / 2)), int(max(0, cy - s / 2))
    x1, y1 = int(min(w, cx + s / 2)), int(min(h, cy + s / 2))
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        crop = frame
    scale = 240.0 / max(crop.shape[:2])
    if scale < 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    rel = Path(ts.strftime("%Y/%m/%d")) / f"{ts.strftime('%H%M%S')}_{tag}.jpg"
    out = root / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    out.write_bytes(buf.tobytes())
    return rel.as_posix()


def purge_snapshots(root: Path, keep_days: int, today: datetime) -> int:
    """Delete evidence pictures older than ``keep_days`` (0 = keep forever)."""
    if keep_days <= 0 or not root.is_dir():
        return 0
    cutoff = (today - timedelta(days=keep_days)).strftime("%Y/%m/%d")
    removed = 0
    for day_dir in sorted(root.glob("*/*/*")):
        rel = day_dir.relative_to(root).as_posix()
        if day_dir.is_dir() and rel < cutoff:
            for f in day_dir.iterdir():
                f.unlink(missing_ok=True)
                removed += 1
            day_dir.rmdir()
    return removed
