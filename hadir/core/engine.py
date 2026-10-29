"""Attendance computation: raw punches + schedules -> daily records.

The engine is pure Python and independent of the database so it can be unit
tested exhaustively.  The database layer loads plain dataclasses defined here.

Key rules
---------
* A schedule gives each calendar day either a shift (start/end, possibly
  ending after midnight) or a rest day.  Weekly schedules repeat every 7 days;
  cycle schedules repeat every N days from an anchor date (e.g. 24h duty then
  48h off).
* Every punch is attached to exactly one *work day*: a punch inside a shift's
  window (start - window_before .. end + window_after) belongs to that shift;
  otherwise it belongs to the calendar day (which starts at ``day_start``,
  04:00 by default, so a 01:30 punch after a night out counts for the previous
  day).  Punches outside every window go to the nearest one.
* First punch = check-in, last punch = check-out.  A single punch is a
  check-in or a check-out depending on which shift edge it is nearer to,
  unless it was explicitly entered as one.
* Hourly leaves excuse lateness/early leaving that they cover.
* Optional strict rules per schedule: arriving more than ``absent_after``
  minutes after the start, leaving before the exit time (``early_absent``) or
  a missing check-in/check-out (``missing_absent``) make the day an absence.
  ``exit_until`` is the last time an exit punch is accepted; a missing
  check-out is final only after it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .timeutil import minutes_between

PRESENT = "present"
ABSENT = "absent"
LEAVE = "leave"
HOLIDAY = "holiday"
REST = "rest"
PENDING = "pending"
FUTURE = "future"
INACTIVE = "inactive"
NO_SCHEDULE = "no_schedule"

STATUS_LABELS = {
    PRESENT: "حاضر",
    ABSENT: "غائب",
    LEAVE: "إجازة",
    HOLIDAY: "عطلة رسمية",
    REST: "عطلة أسبوعية",
    PENDING: "لم يحضر بعد",
    FUTURE: "—",
    INACTIVE: "خارج الخدمة",
    NO_SCHEDULE: "بلا جدول",
}

FLAG_LABELS = {
    "missing_in": "لم يسجّل حضوراً",
    "missing_out": "لم يسجّل انصرافاً",
    "odd_punches": "عدد بصمات فردي",
    "manual": "بصمة يدوية",
    "corrected": "معدّل",
    "worked_off_day": "عمل في يوم عطلة",
    "worked_on_leave": "حضر أثناء الإجازة",
    "at_work": "في الدوام",
    "low_liveness": "تحقق حيوية ضعيف",
    "overdue": "تجاوز وقت الحضور",
    "absent_late": "حضر بعد وقت الغياب",
    "absent_early": "خرج قبل وقت الخروج",
}

# Why a day with punches still counts as an absence, in the order they are reported.
ABSENCE_REASONS = ("absent_late", "absent_early", "missing_in", "missing_out")


# --------------------------------------------------------------------------
# Input model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DayRule:
    workday: bool
    start: time | None = None
    end: time | None = None


@dataclass(frozen=True)
class Shift:
    date: date
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return minutes_between(self.start, self.end)


@dataclass
class Schedule:
    id: int
    name: str
    kind: str = "weekly"  # "weekly" | "cycle"
    days: tuple[DayRule, ...] = ()
    cycle_anchor: date | None = None
    grace_late: int = 10
    grace_early: int = 0
    late_after_grace: bool = False
    window_before: int = 240
    window_after: int = 480
    overtime_min: int = 30
    absent_after: int = 0  # minutes after the start; a later arrival is an absence (0 = off)
    exit_until: time | None = None  # last exit punch; a missing check-out is final after it
    early_absent: bool = False  # leaving before the exit time is an absence, not an early leave
    missing_absent: bool = False  # a missing check-in or check-out makes the day an absence

    def rule_for(self, d: date) -> DayRule | None:
        if not self.days:
            return None
        if self.kind == "cycle":
            anchor = self.cycle_anchor or date(2000, 1, 2)
            return self.days[(d - anchor).days % len(self.days)]
        return self.days[d.weekday()]

    def shift_for(self, d: date) -> Shift | None:
        rule = self.rule_for(d)
        if rule is None or not rule.workday or rule.start is None or rule.end is None:
            return None
        start = datetime.combine(d, rule.start)
        end = datetime.combine(d, rule.end)
        if end <= start:
            end += timedelta(days=1)
        return Shift(d, start, end)

    def exit_deadline(self, shift: Shift) -> datetime | None:
        """The first ``exit_until`` at or after the end of the shift."""
        if self.exit_until is None:
            return None
        deadline = datetime.combine(shift.end.date(), self.exit_until)
        if deadline < shift.end:
            deadline += timedelta(days=1)
        return deadline


@dataclass(frozen=True)
class Assignment:
    schedule_id: int
    start: date
    end: date | None = None

    def covers(self, d: date) -> bool:
        return self.start <= d and (self.end is None or d <= self.end)


@dataclass(frozen=True)
class GroupPeriod:
    group_id: int
    start: date
    end: date | None = None

    def covers(self, d: date) -> bool:
        return self.start <= d and (self.end is None or d <= self.end)


@dataclass(frozen=True)
class Holiday:
    id: int
    name: str
    start: date
    end: date
    group_ids: frozenset = frozenset()  # empty: applies to everybody

    def applies(self, d: date, group_id: int | None) -> bool:
        if not (self.start <= d <= self.end):
            return False
        return not self.group_ids or group_id in self.group_ids


@dataclass(frozen=True)
class LeaveType:
    id: int
    name: str
    unit: str = "day"  # "day" | "hour"
    paid: bool = True
    counts_present: bool = False
    calendar_days: bool = False
    short: str = "ج"


@dataclass(frozen=True)
class Leave:
    id: int
    type: LeaveType
    start: date
    end: date
    start_time: time | None = None
    end_time: time | None = None
    note: str = ""

    @property
    def hourly(self) -> bool:
        return self.type.unit == "hour" or self.start_time is not None

    def interval(self) -> tuple[datetime, datetime]:
        s = datetime.combine(self.start, self.start_time or time(0))
        e = datetime.combine(self.start, self.end_time or time(0))
        if e <= s:
            e += timedelta(days=1)
        return s, e


@dataclass(frozen=True)
class Punch:
    id: int
    ts: datetime
    kind: str = "auto"  # "auto" | "in" | "out"
    source: str = "face"  # "face" | "manual" | "pin" | "import"
    corrected: bool = False
    score: float | None = None
    liveness: float | None = None


@dataclass
class EmployeeData:
    id: int
    name: str = ""
    code: str = ""
    hire_date: date | None = None
    end_date: date | None = None
    groups: list[GroupPeriod] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    leaves: list[Leave] = field(default_factory=list)
    punches: list[Punch] = field(default_factory=list)

    def active_on(self, d: date) -> bool:
        if self.hire_date and d < self.hire_date:
            return False
        if self.end_date and d > self.end_date:
            return False
        return True

    def group_on(self, d: date) -> int | None:
        best = None
        for g in self.groups:
            if g.covers(d) and (best is None or g.start >= best.start):
                best = g
        return best.group_id if best else None


@dataclass
class OrgData:
    schedules: dict = field(default_factory=dict)  # id -> Schedule
    group_assignments: dict = field(default_factory=dict)  # group id -> [Assignment]
    holidays: list = field(default_factory=list)
    day_start: time = time(4, 0)
    dedupe_seconds: int = 120
    low_liveness: float = 0.0

    def schedule_for(self, emp: EmployeeData, d: date, group_id: int | None) -> Schedule | None:
        best = _latest(a for a in emp.assignments if a.covers(d))
        if best is None and group_id is not None:
            best = _latest(a for a in self.group_assignments.get(group_id, ()) if a.covers(d))
        return self.schedules.get(best.schedule_id) if best else None


def _latest(items):
    best = None
    for a in items:
        if best is None or a.start >= best.start:
            best = a
    return best


# --------------------------------------------------------------------------
# Output model
# --------------------------------------------------------------------------
@dataclass
class DayRecord:
    employee_id: int
    date: date
    status: str
    group_id: int | None = None
    schedule: Schedule | None = None
    shift: Shift | None = None
    punches: list = field(default_factory=list)
    check_in: datetime | None = None
    check_out: datetime | None = None
    late: int = 0
    early: int = 0
    worked: int = 0
    overtime: int = 0
    required: int = 0
    leave: Leave | None = None
    hourly_leaves: list = field(default_factory=list)
    hourly_leave_minutes: int = 0
    holiday: Holiday | None = None
    flags: set = field(default_factory=set)

    @property
    def is_workday(self) -> bool:
        return self.shift is not None and self.status not in (HOLIDAY, INACTIVE)

    @property
    def counts_present(self) -> bool:
        return self.status == PRESENT or (
            self.status == LEAVE and self.leave is not None and self.leave.type.counts_present)

    @property
    def absence_reason(self) -> str:
        """Why a day with punches counts as an absence ("" if it does not)."""
        if self.status != ABSENT:
            return ""
        return next((FLAG_LABELS[f] for f in ABSENCE_REASONS if f in self.flags), "")

    @property
    def label(self) -> str:
        if self.status == LEAVE and self.leave is not None:
            return self.leave.type.name
        if self.status == HOLIDAY and self.holiday is not None:
            return self.holiday.name
        if self.status == ABSENT and self.absence_reason:
            return f"{STATUS_LABELS[ABSENT]} ({self.absence_reason})"
        if self.status == PRESENT:
            parts = []
            if self.late:
                parts.append("متأخر")
            if self.early:
                parts.append("خروج مبكر")
            if "missing_out" in self.flags:
                parts.append(FLAG_LABELS["missing_out"])
            if "missing_in" in self.flags:
                parts.append(FLAG_LABELS["missing_in"])
            if "at_work" in self.flags and not parts:
                parts.append(FLAG_LABELS["at_work"])
            return "، ".join(parts) if parts else STATUS_LABELS[PRESENT]
        return STATUS_LABELS.get(self.status, self.status)


@dataclass
class _Slot:
    date: date
    shift: Shift | None
    ws: datetime
    we: datetime
    punches: list = field(default_factory=list)

    def contains(self, ts: datetime) -> bool:
        if self.shift is not None:
            return self.ws <= ts <= self.we
        return self.ws <= ts < self.we

    def distance(self, ts: datetime) -> float:
        if self.contains(ts):
            return 0.0
        return min(abs((ts - self.ws).total_seconds()), abs((ts - self.we).total_seconds()))

    def anchor_distance(self, ts: datetime) -> float:
        if self.shift is None:
            return self.distance(ts)
        return min(abs((ts - self.shift.start).total_seconds()),
                   abs((ts - self.shift.end).total_seconds()))


# --------------------------------------------------------------------------
# Computation
# --------------------------------------------------------------------------
def dedupe(punches: list[Punch], seconds: int) -> list[Punch]:
    out: list[Punch] = []
    for p in sorted(punches, key=lambda p: (p.ts, p.id)):
        if out and (p.ts - out[-1].ts).total_seconds() < seconds:
            continue
        out.append(p)
    return out


def compute_days(emp: EmployeeData, org: OrgData, start: date, end: date,
                 now: datetime | None = None) -> list[DayRecord]:
    """Compute one DayRecord per calendar day in [start, end]."""
    first, last = start - timedelta(days=1), end + timedelta(days=1)
    ctx = {}
    slots: list[_Slot] = []
    d = first
    while d <= last:
        group_id = emp.group_on(d)
        schedule = org.schedule_for(emp, d, group_id)
        shift = schedule.shift_for(d) if schedule else None
        if shift is not None:
            ws = shift.start - timedelta(minutes=schedule.window_before)
            we = shift.end + timedelta(minutes=schedule.window_after)
            deadline = schedule.exit_deadline(shift)
            if deadline is not None and deadline > we:
                we = deadline
        else:
            ws = datetime.combine(d, org.day_start)
            we = ws + timedelta(days=1)
        slot = _Slot(d, shift, ws, we)
        slots.append(slot)
        ctx[d] = (group_id, schedule, slot)
        d += timedelta(days=1)

    by_date = {s.date: s for s in slots}
    day_offset = timedelta(hours=org.day_start.hour, minutes=org.day_start.minute)
    for p in dedupe(emp.punches, org.dedupe_seconds):
        inside = [s for s in slots if s.contains(p.ts)]
        with_shift = [s for s in inside if s.shift is not None]
        cands = with_shift or inside
        if cands:
            # Prefer the shift actually running at that moment (hand-over between back to
            # back shifts), then the shift whose start/end is nearest.
            slot = min(cands, key=lambda s: (
                not (s.shift and s.shift.start <= p.ts < s.shift.end),
                s.anchor_distance(p.ts), s.date))
        else:
            # Between windows: the logical calendar day (which begins at day_start) decides.
            slot = by_date.get((p.ts - day_offset).date()) or min(
                slots, key=lambda s: (s.distance(p.ts), s.date))
        slot.punches.append(p)

    records = []
    d = start
    while d <= end:
        group_id, schedule, slot = ctx[d]
        records.append(_compute_day(emp, org, d, group_id, schedule, slot, now))
        d += timedelta(days=1)
    return records


def _hourly_intervals(leaves, shift: Shift | None, d: date):
    out = []
    for lv in leaves:
        if not lv.hourly:
            continue
        s, e = lv.interval()
        if shift is not None:
            if s < shift.end and e > shift.start:
                out.append((s, e, lv))
        elif lv.start == d:
            out.append((s, e, lv))
    out.sort(key=lambda x: x[0])
    return out


def _compute_day(emp: EmployeeData, org: OrgData, d: date, group_id, schedule,
                 slot: _Slot, now: datetime | None) -> DayRecord:
    shift = slot.shift
    punches = sorted(slot.punches, key=lambda p: p.ts)
    rec = DayRecord(emp.id, d, PRESENT, group_id=group_id, schedule=schedule, shift=shift,
                    punches=punches)
    today = now.date() if now else None
    # Is the work day finished (so a missing punch is final)?
    shift_over = now is None or now >= (shift.end if shift is not None else slot.we)
    for p in punches:
        if p.source != "face":
            rec.flags.add("manual")
        if p.corrected:
            rec.flags.add("corrected")
        if (org.low_liveness and p.source == "face" and p.liveness is not None
                and p.liveness < org.low_liveness):
            rec.flags.add("low_liveness")

    # check-in / check-out
    if len(punches) >= 2:
        rec.check_in, rec.check_out = punches[0].ts, punches[-1].ts
        if len(punches) % 2 == 0:
            rec.worked = sum(minutes_between(punches[i].ts, punches[i + 1].ts)
                             for i in range(0, len(punches), 2))
        else:
            rec.worked = minutes_between(punches[0].ts, punches[-1].ts)
            rec.flags.add("odd_punches")
    elif len(punches) == 1:
        p = punches[0]
        # While the shift is still running a lone punch is the arrival; once it is over the
        # punch is read as whichever shift edge it is nearer to.
        as_out = p.kind == "out" or (
            p.kind != "in" and shift is not None and shift_over
            and abs((p.ts - shift.end).total_seconds()) < abs((p.ts - shift.start).total_seconds()))
        if as_out:
            rec.check_out = p.ts
        else:
            rec.check_in = p.ts

    day_leaves = [lv for lv in emp.leaves if not lv.hourly and lv.start <= d <= lv.end]
    full_leave = day_leaves[0] if day_leaves else None
    holiday = next((h for h in org.holidays if h.applies(d, group_id)), None)
    hourly = _hourly_intervals(emp.leaves, shift, d)
    rec.hourly_leaves = [h[2] for h in hourly]

    if not emp.active_on(d):
        rec.status = INACTIVE
        return rec
    if schedule is None:
        if full_leave:
            rec.status, rec.leave = LEAVE, full_leave
        elif not punches and today is not None and d > today:
            rec.status = FUTURE
        else:
            rec.status = NO_SCHEDULE
            _overtime_off_day(rec, schedule, shift_over)
        return rec

    if holiday and not (full_leave and full_leave.type.calendar_days):
        rec.status, rec.holiday = HOLIDAY, holiday
        if punches:
            rec.flags.add("worked_off_day")
            _overtime_off_day(rec, schedule, shift_over)
        return rec
    if full_leave and (shift is not None or full_leave.type.calendar_days):
        rec.status, rec.leave = LEAVE, full_leave
        if punches:
            rec.flags.add("worked_on_leave")
        return rec
    if shift is None:
        rec.status = REST
        if punches:
            rec.flags.add("worked_off_day")
            _overtime_off_day(rec, schedule, shift_over)
        return rec

    # A working day.
    rec.required = shift.minutes
    covered = _covered_minutes(hourly, shift)
    rec.hourly_leave_minutes = covered
    if covered >= rec.required and rec.required > 0 and not punches:
        rec.status, rec.leave = LEAVE, hourly[0][2]
        return rec

    # An hourly leave at the start of the day moves the start (and the absence cutoff) with it.
    eff_start = shift.start
    grace = timedelta(minutes=schedule.grace_late)
    for s, e, _ in hourly:
        if s <= eff_start + grace and e > eff_start:
            eff_start = e
    cutoff = eff_start + timedelta(minutes=schedule.absent_after) if schedule.absent_after else None

    if not punches:
        if today is not None and d > today:
            rec.status = FUTURE
        elif not shift_over and (cutoff is None or now <= cutoff):
            rec.status = PENDING
            if now >= shift.start + timedelta(minutes=schedule.grace_late):
                rec.flags.add("overdue")
        else:
            rec.status = ABSENT
        return rec

    rec.status = PRESENT
    absence = []
    if rec.check_in is not None:
        if cutoff is not None and rec.check_in > cutoff:
            absence.append("absent_late")
        elif rec.check_in > eff_start + grace:
            base = eff_start + grace if schedule.late_after_grace else eff_start
            rec.late = minutes_between(base, rec.check_in)
    else:
        rec.flags.add("missing_in")
        if schedule.missing_absent:
            absence.append("missing_in")

    # A check-out is still possible until the last exit time.
    deadline = schedule.exit_deadline(shift)
    exit_closed = now is None or now >= (deadline or shift.end)
    if rec.check_out is not None:
        eff_end = shift.end
        grace = timedelta(minutes=schedule.grace_early)
        for s, e, _ in sorted(hourly, key=lambda x: x[1], reverse=True):
            if e >= eff_end - grace and s < eff_end:
                eff_end = s
        if rec.check_out < eff_end - grace:
            if schedule.early_absent:
                absence.append("absent_early")
            else:
                rec.early = minutes_between(rec.check_out, eff_end)
        if rec.check_out > shift.end:
            extra = minutes_between(shift.end, rec.check_out)
            if extra >= schedule.overtime_min:
                rec.overtime = extra
    elif exit_closed:
        rec.flags.add("missing_out")
        if schedule.missing_absent:
            absence.append("missing_out")
    else:
        rec.flags.add("at_work")

    if absence:
        rec.status = ABSENT
        rec.flags.update(f for f in absence if f.startswith("absent_"))
        rec.late = rec.early = rec.overtime = 0
    return rec


def _covered_minutes(hourly, shift: Shift) -> int:
    total = 0
    cursor = shift.start
    for s, e, _ in hourly:
        s, e = max(s, cursor), min(e, shift.end)
        if e > s:
            total += minutes_between(s, e)
            cursor = e
    return total


def _overtime_off_day(rec: DayRecord, schedule: Schedule | None, day_over: bool) -> None:
    threshold = schedule.overtime_min if schedule else 0
    if rec.worked >= max(threshold, 1):
        rec.overtime = rec.worked
    if len(rec.punches) == 1:
        rec.flags.add("missing_out" if day_over else "at_work")


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
@dataclass
class Summary:
    days: int = 0
    workdays: int = 0
    present_days: int = 0
    absent_days: int = 0
    leave_days: int = 0
    leave_by_type: dict = field(default_factory=dict)  # name -> days
    holiday_days: int = 0
    rest_days: int = 0
    late_count: int = 0
    late_minutes: int = 0
    early_count: int = 0
    early_minutes: int = 0
    missing_count: int = 0
    worked_minutes: int = 0
    required_minutes: int = 0
    overtime_minutes: int = 0
    hourly_leave_minutes: int = 0
    off_day_work_days: int = 0

    @property
    def attendance_rate(self) -> float | None:
        due = self.present_days + self.absent_days
        return None if due == 0 else self.present_days / due


def summarize(records: list[DayRecord]) -> Summary:
    s = Summary()
    for r in records:
        if r.status in (FUTURE, INACTIVE):
            continue
        s.days += 1
        if r.shift is not None and r.status not in (HOLIDAY, REST):
            s.workdays += 1
        if r.counts_present:
            s.present_days += 1
        if r.status == ABSENT:
            s.absent_days += 1
        elif r.status == LEAVE and r.leave is not None:
            s.leave_days += 1
            name = r.leave.type.name
            s.leave_by_type[name] = s.leave_by_type.get(name, 0) + 1
        elif r.status == HOLIDAY:
            s.holiday_days += 1
        elif r.status == REST:
            s.rest_days += 1
        if r.late:
            s.late_count += 1
            s.late_minutes += r.late
        if r.early:
            s.early_count += 1
            s.early_minutes += r.early
        if "missing_in" in r.flags or "missing_out" in r.flags:
            s.missing_count += 1
        if "worked_off_day" in r.flags:
            s.off_day_work_days += 1
        s.worked_minutes += r.worked
        s.required_minutes += r.required
        s.overtime_minutes += r.overtime
        s.hourly_leave_minutes += r.hourly_leave_minutes
    return s


def classify_new_punch(emp: EmployeeData, org: OrgData, ts: datetime) -> tuple[str, DayRecord]:
    """Predict whether a punch at ``ts`` will be read as check-in or check-out.

    Returns ("in" | "out", the resulting day record).
    """
    probe = Punch(id=-1, ts=ts)
    trial = EmployeeData(emp.id, emp.name, emp.code, emp.hire_date, emp.end_date, emp.groups,
                         emp.assignments, emp.leaves, list(emp.punches) + [probe])
    days = compute_days(trial, org, ts.date() - timedelta(days=1), ts.date() + timedelta(days=1),
                        now=ts)
    for rec in days:
        if any(p.id == -1 for p in rec.punches):
            if rec.check_out == ts and rec.check_in != ts:
                return "out", rec
            return "in", rec
    # The punch was swallowed by de-duplication: report the day it falls in.
    rec = min(days, key=lambda r: abs((r.date - ts.date()).days))
    return ("out" if rec.check_out else "in"), rec
