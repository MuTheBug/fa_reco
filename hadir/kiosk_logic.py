"""What happens when the kiosk recognises (or fails to recognise) someone."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .core import engine as E
from .core.timeutil import digits, fmt_minutes, fmt_time, parse_dt
from .db.store import Database
from .face.pipeline import Decision, FaceEngine
from .face.service import maybe_adapt, save_snapshot
from .notify import telegram
from .paths import snapshots_dir


@dataclass
class KioskResult:
    kind: str  # punched | duplicate | unknown | spoof | ambiguous | inactive | error
    tone: str  # ok | warn | bad
    title: str
    lines: list = field(default_factory=list)
    employee_id: int | None = None
    name: str = ""
    code: str = ""
    punch_type: str = ""  # in | out
    ts: datetime | None = None
    punch_id: int | None = None
    adapted: bool = False


def _minutes_text(m: int) -> str:
    if m < 60:
        return f"{digits(m)} دقيقة"
    return f"{fmt_minutes(m)} ساعة"


def describe_day(rec: E.DayRecord, punch_type: str, ts: datetime) -> list[str]:
    """Short Arabic feedback about the punch just made."""
    lines = []
    if rec.status == E.HOLIDAY:
        lines.append(f"اليوم عطلة رسمية ({rec.holiday.name})" if rec.holiday else "اليوم عطلة رسمية")
    elif rec.status == E.REST:
        lines.append("اليوم عطلة أسبوعية في جدولك")
    elif rec.status == E.LEAVE:
        lines.append(f"لديك {rec.leave.type.name} اليوم" if rec.leave else "لديك إجازة اليوم")
    elif rec.shift is not None:
        absent = rec.status == E.ABSENT
        if punch_type == "in":
            if absent and "absent_late" in rec.flags:
                lines.append("حضور بعد وقت الغياب — يُحتسب اليوم غياباً")
            elif rec.late:
                lines.append(f"متأخر {_minutes_text(rec.late)}")
            else:
                lines.append("في الموعد — يوم موفق")
            lines.append(f"دوامك من {fmt_time(rec.shift.start)} إلى {fmt_time(rec.shift.end)}")
        else:
            if absent and "absent_early" in rec.flags:
                lines.append(f"خروج قبل الساعة {fmt_time(rec.shift.end)} — يُحتسب غياباً ما لم تكن لديك إجازة")
            elif absent and "absent_late" in rec.flags:
                lines.append("حضرت اليوم بعد وقت الغياب — اليوم محسوب غياباً")
            elif rec.early:
                lines.append(f"خروج مبكر بـ {_minutes_text(rec.early)}")
            elif rec.overtime:
                lines.append(f"عمل إضافي {_minutes_text(rec.overtime)}")
            if rec.check_in:
                lines.append(f"الحضور {fmt_time(rec.check_in)} — مدة الدوام {fmt_minutes(rec.worked)}")
            elif absent:
                lines.append("لا يوجد تسجيل حضور لهذا اليوم — يُحتسب غياباً")
            else:
                lines.append("لا يوجد تسجيل حضور لهذا اليوم")
    return lines


class KioskLogic:
    def __init__(self, db: Database, engine: FaceEngine | None = None):
        self.db = db
        self.engine = engine
        self._last_unknown_event = 0.0

    def settings(self, key):
        return self.db.settings.get(key)

    def _snapshot(self, frame, box, ts: datetime, tag: str) -> str | None:
        if frame is None or box is None or not self.settings("kiosk.save_snapshots"):
            return None
        try:
            return save_snapshot(snapshots_dir(), frame, box, ts, tag)
        except OSError:
            return None

    def register(self, employee_id: int, ts: datetime, *, source: str = "face",
                 score: float | None = None, liveness: float | None = None, frame=None,
                 box=None) -> KioskResult:
        emp = self.db.employees.get(employee_id)
        if emp is None:
            return KioskResult("error", "bad", "الموظف غير موجود")
        if not emp["active"]:
            return KioskResult("inactive", "bad", "هذا الموظف غير فعّال", ["راجع مسؤول النظام"],
                               employee_id, emp["full_name"], emp["code"])
        dup = int(self.settings("kiosk.duplicate_minutes") or 0)
        last = self.db.punches.last_for(employee_id)
        if last is not None and dup > 0:
            gap = ts - parse_dt(last["ts"])
            if timedelta(0) <= gap < timedelta(minutes=dup):
                mins = max(1, int(gap.total_seconds() // 60))
                return KioskResult("duplicate", "warn", "تم تسجيلك مسبقاً",
                                   [f"آخر تسجيل قبل {digits(mins)} دقيقة الساعة {fmt_time(parse_dt(last['ts']))}"],
                                   employee_id, emp["full_name"], emp["code"], ts=ts)
        [data] = self.db.load_employees([employee_id], ts.date() - timedelta(days=1), ts.date())
        punch_type, rec = E.classify_new_punch(data, self.db.load_org(), ts)
        deadline = rec.schedule.exit_deadline(rec.shift) if rec.schedule and rec.shift else None
        if deadline is not None and ts > deadline:
            # No punches once the day's last exit time has passed; the admin sees the attempt.
            snap = self._snapshot(frame, box, ts, "late")
            self.db.events.add("late_punch", f"محاولة بصمة الساعة {fmt_time(ts)} بعد آخر وقت للخروج"
                               f" ({fmt_time(deadline)})", employee_id, snap)
            return KioskResult("closed", "bad", "انتهى وقت تسجيل الخروج لهذا اليوم",
                               [f"آخر وقت للبصمة كان الساعة {fmt_time(deadline)}",
                                "راجع مسؤول النظام إن احتجت إلى تصحيح"],
                               employee_id, emp["full_name"], emp["code"], ts=ts)
        snap = self._snapshot(frame, box, ts, f"{emp['code']}")
        if source == "face":
            pid = self.db.punches.add_face(employee_id, ts, score, liveness, snap)
        else:
            pid = self.db.punches.add_face(employee_id, ts, None, None, snap, source=source)
        telegram.queue_punch(self.db, emp, punch_type, rec, ts)
        title = "تم تسجيل الحضور" if punch_type == "in" else "تم تسجيل الانصراف"
        # The punch is recorded either way; an absence under the rules is shown as a warning.
        tone = "warn" if rec.status == E.ABSENT else "ok"
        return KioskResult("punched", tone, title, describe_day(rec, punch_type, ts), employee_id,
                           emp["full_name"], emp["code"], punch_type, ts, pid)

    def handle(self, decision: Decision, frame, ts: datetime, security_threshold: float) -> KioskResult:
        box = decision.best.face.box if decision.best is not None else None
        if decision.kind == "accept":
            res = self.register(decision.employee_id, ts, score=round(decision.score, 4),
                                liveness=None if decision.liveness is None else round(decision.liveness, 4),
                                frame=decision.best.frame if decision.best else frame, box=box)
            if res.kind == "punched" and self.engine is not None and self.settings("recog.adaptive"):
                try:
                    res.adapted = maybe_adapt(self.db, self.engine, decision, security_threshold)
                except Exception:
                    res.adapted = False
            return res
        if decision.kind == "spoof":
            snap = self._snapshot(frame, box, ts, "spoof")
            name = self.db.employees.names().get(decision.employee_id, "")
            self.db.events.add("spoof", f"محاولة تسجيل بصورة أو شاشة (تشبه: {name})" if name else
                               "محاولة تسجيل بصورة أو شاشة", decision.employee_id, snap)
            return KioskResult("spoof", "bad", "تعذّر التحقق من أنك أمام الكاميرا فعلاً",
                               ["لا تستخدم صورة أو هاتفاً — قف بنفسك أمام الكاميرا"])
        if decision.kind == "ambiguous":
            snap = self._snapshot(frame, box, ts, "ambiguous")
            names = self.db.employees.names()
            self.db.events.add("ambiguous", "تعذّر التمييز بين: " + "، ".join(
                n for n in (names.get(decision.employee_id), names.get(decision.runner_up)) if n),
                decision.employee_id, snap)
            return KioskResult("ambiguous", "warn", "لم يتم التأكد من هويتك",
                               ["انظر مباشرة إلى الكاميرا وحاول مرة أخرى",
                                "إذا تكرر ذلك راجع مسؤول النظام لإضافة عينات لوجهك"])
        # unknown
        now = time.monotonic()
        if now - self._last_unknown_event > 30:
            self._last_unknown_event = now
            snap = self._snapshot(frame, box, ts, "unknown")
            self.db.events.add("unknown_face", f"أعلى تشابه {decision.score:.2f}", None, snap)
        return KioskResult("unknown", "bad", "لم يتم التعرّف عليك",
                           ["اقترب من الكاميرا وانظر إليها مباشرة",
                            "إذا لم تكن مسجلاً راجع مسؤول النظام"])
