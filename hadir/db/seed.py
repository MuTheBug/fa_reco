"""Default data created on first run."""

from __future__ import annotations

from .schema import (ADMIN_GROUP, DEFAULT_GROUPS, GUARD_RULES, SEED_END, SEED_RULES, SEED_SCHEDULE,
                     SEED_START, rotation_anchor)
from .store import Database

DEFAULT_LEAVE_TYPES = [
    dict(name="إجازة اعتيادية", short="ج", unit="day", paid=True, quota=36, color="#2563eb"),
    dict(name="إجازة مرضية", short="م", unit="day", paid=True, color="#dc2626"),
    dict(name="إجازة زمنية", short="ز", unit="hour", paid=True, quota=7, quota_period="month",
         color="#0891b2"),
    dict(name="إجازة بدون راتب", short="ب", unit="day", paid=False, color="#64748b"),
    dict(name="إجازة أمومة", short="أ", unit="day", paid=True, calendar_days=True, color="#db2777"),
    dict(name="واجب رسمي / إيفاد", short="و", unit="day", paid=True, counts_present=True,
         color="#16a34a"),
]


def seed_defaults(db: Database) -> None:
    """Create leave types, the schedules and the three groups (idempotent)."""
    with db.tx():
        if not db.scalar("SELECT COUNT(*) FROM leave_types"):
            for t in DEFAULT_LEAVE_TYPES:
                db.leaves.save_type(t)
        if not db.scalar("SELECT COUNT(*) FROM schedules"):
            work = (True, SEED_START, SEED_END)
            off = (False, None, None)
            # index = date.weekday(): Mon..Sun; Friday and Saturday are the weekend.
            days = [work, work, work, work, off, off, work]
            sid = db.schedules.save(dict(name=SEED_SCHEDULE, kind="weekly", days=days, **SEED_RULES))
            if not db.scalar("SELECT COUNT(*) FROM emp_groups"):
                gid = db.groups.save(ADMIN_GROUP, "الدوام الرسمي")
                db.groups.assign_schedule(gid, sid, "2000-01-01")
                today = db.now().date()
                for group, description, schedule, days, continuous in DEFAULT_GROUPS:
                    anchor = rotation_anchor(today) if continuous else today.isoformat()
                    sid = db.schedules.save(dict(name=schedule, kind="cycle", cycle_anchor=anchor, days=days,
                                                 continuous=continuous, **GUARD_RULES))
                    gid = db.groups.save(group, description)
                    db.groups.assign_schedule(gid, sid, "2000-01-01")
