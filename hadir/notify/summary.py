"""The day's attendance in numbers, for the Telegram report: no names, only how many.

A department with many employees does not want a line (or a message) per person: the manager
wants to see at a glance how many came on time, how many were late, absent or on leave, and
where (by group).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, datetime, time

from ..core import engine as E
from ..core.timeutil import digits, fmt_date, fmt_minutes, fmt_time, weekday_name

# Why someone counts as absent, in the order shown.
ABSENCE_KINDS = {
    "no_show": "لم يحضروا",
    "absent_late": "حضروا بعد وقت الغياب",
    "absent_early": "خرجوا قبل وقت الخروج",
    "missing_out": "لم يبصموا للخروج",
    "missing_in": "لم يبصموا للحضور",
}


@dataclass
class Counts:
    due: int = 0  # expected at work: everyone except those on a holiday or a day off
    on_time: int = 0
    late: int = 0
    late_minutes: int = 0
    absent: int = 0
    leave: int = 0  # a whole day of leave
    pending: int = 0  # not arrived yet, before the absence time
    off: int = 0  # official holiday, weekly day off or rest between guard duties
    early: int = 0  # left early (inside the exit window)
    left_on_leave: int = 0  # left on an hourly leave until the end of the day
    absences: dict = field(default_factory=dict)  # ABSENCE_KINDS key -> how many

    @property
    def present(self) -> int:
        return self.on_time + self.late

    @property
    def rate(self) -> float | None:
        """Share present of those whose day is settled (leave and not-yet-arrived left out)."""
        settled = self.present + self.absent
        return None if settled == 0 else self.present / settled

    def add(self, rec: E.DayRecord) -> None:
        if rec.status in (E.HOLIDAY, E.REST):
            self.off += 1
            return
        if rec.status in (E.INACTIVE, E.FUTURE, E.NO_SCHEDULE):
            return
        self.due += 1
        if rec.status == E.LEAVE:
            self.leave += 1
        elif rec.status == E.PENDING:
            self.pending += 1
        elif rec.status == E.ABSENT:
            self.absent += 1
            kind = next((f for f in ABSENCE_KINDS if f in rec.flags), "no_show")
            self.absences[kind] = self.absences.get(kind, 0) + 1
        else:
            if rec.late:
                self.late += 1
                self.late_minutes += rec.late
            else:
                self.on_time += 1
            if rec.early:
                self.early += 1
            if "left_on_leave" in rec.flags:
                self.left_on_leave += 1


@dataclass
class DaySummary:
    day: date
    at: time | None
    org: str
    total: Counts
    groups: list  # [(group name, Counts)] in the order of the groups

    @property
    def when(self) -> str:
        return f"{weekday_name(self.day)} {fmt_date(self.day)}"


def day_summary(db, day: date, at: time | None = None, now: datetime | None = None) -> DaySummary | None:
    """The numbers of ``day`` as they stand at ``now``; None when nobody is due at work."""
    from ..reports.sources import select_employees

    people = db.compute(select_employees(db, day, day), day, day, now=now or db.now())
    names = {g["id"]: g["name"] for g in db.q("SELECT id, name FROM emp_groups ORDER BY id")}
    groups: dict[str, Counts] = {name: Counts() for name in names.values()}
    total = Counts()
    for _, [rec] in people:
        total.add(rec)
        groups.setdefault(names.get(rec.group_id, "بدون مجموعة"), Counts()).add(rec)
    if not total.due:
        return None
    return DaySummary(day, at, db.settings.get("org.name") or "", total,
                      [(n, c) for n, c in groups.items() if c.due or c.off])


def percent(value: float | None) -> str:
    return "—" if value is None else f"{digits(round(value * 100))}%"


def minutes_text(m: int) -> str:
    return f"{digits(m)} دقيقة" if m < 60 else f"{fmt_minutes(m)} ساعة"


def caption(s: DaySummary) -> str:
    """The text under the picture (and in the notification): Telegram HTML."""
    t = s.total
    head = f"<b>📊 ملخص الدوام — {s.when}</b>"
    sub = html.escape(s.org) + (f" • حتى الساعة {fmt_time(s.at)}" if s.at else "")
    lines = [head, sub, "",
             f"✅ حضر <b>{digits(t.present)}</b> من {digits(t.due)} ({percent(t.rate)})",
             f"⏰ متأخرون: <b>{digits(t.late)}</b>"
             + (f" (معدل التأخير {minutes_text(round(t.late_minutes / t.late))})" if t.late else ""),
             f"❌ غائبون: <b>{digits(t.absent)}</b>"]
    if t.leave:
        lines.append(f"🏖 في إجازة: <b>{digits(t.leave)}</b>")
    if t.pending:
        lines.append(f"⏳ لم يصلوا بعد: <b>{digits(t.pending)}</b>")
    return "\n".join(lines)


def report_text(s: DaySummary) -> str:
    """The same summary as text only (when the picture cannot be made)."""
    t = s.total
    lines = [caption(s)]
    if t.absent:
        lines.append("الغياب: " + "، ".join(f"{label} {digits(t.absences[k])}"
                                             for k, label in ABSENCE_KINDS.items() if t.absences.get(k)))
    if t.early or t.left_on_leave:
        lines.append(f"خروج مبكر: {digits(t.early)} • غادروا بإجازة ساعية: {digits(t.left_on_leave)}")
    lines.append("")
    for name, c in s.groups:
        if c.due:
            lines.append(f"👥 {html.escape(name)}: حضر {digits(c.present)} من {digits(c.due)}"
                         f" • متأخر {digits(c.late)} • غائب {digits(c.absent)} • إجازة {digits(c.leave)}")
        else:
            lines.append(f"👥 {html.escape(name)}: لا دوام اليوم")
    return "\n".join(lines)
