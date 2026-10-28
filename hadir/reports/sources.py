"""Row producers ("data sources") with their field catalogues.

Both the ready-made reports and the custom report builder use these.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..core import engine as E
from ..core.timeutil import month_bounds, parse_date, parse_dt, weekday_name
from ..db.store import Database

# key, Arabic title, kind, default total
DAILY_FIELDS = [
    ("date", "التاريخ", "date", ""),
    ("weekday", "اليوم", "text", ""),
    ("code", "الرقم الوظيفي", "text", ""),
    ("name", "الاسم", "text", ""),
    ("group", "المجموعة", "text", ""),
    ("department", "القسم", "text", ""),
    ("job_title", "العنوان الوظيفي", "text", ""),
    ("schedule", "جدول الدوام", "text", ""),
    ("shift_start", "بداية الدوام", "time", ""),
    ("shift_end", "نهاية الدوام", "time", ""),
    ("check_in", "الحضور", "time", ""),
    ("check_out", "الانصراف", "time", ""),
    ("status", "الحالة", "text", ""),
    ("late", "التأخير", "minutes", "sum"),
    ("early", "الخروج المبكر", "minutes", "sum"),
    ("worked", "مدة العمل", "minutes", "sum"),
    ("overtime", "الإضافي", "minutes", "sum"),
    ("required", "المطلوب", "minutes", "sum"),
    ("hourly_leave", "إجازة زمنية", "minutes", "sum"),
    ("leave_type", "نوع الإجازة", "text", ""),
    ("holiday", "العطلة", "text", ""),
    ("punch_count", "عدد البصمات", "int", "sum"),
    ("notes", "ملاحظات", "text", ""),
]

SUMMARY_FIELDS = [
    ("code", "الرقم الوظيفي", "text", ""),
    ("name", "الاسم", "text", ""),
    ("group", "المجموعة", "text", ""),
    ("department", "القسم", "text", ""),
    ("job_title", "العنوان الوظيفي", "text", ""),
    ("workdays", "أيام الدوام", "int", "sum"),
    ("present", "أيام الحضور", "int", "sum"),
    ("absent", "أيام الغياب", "int", "sum"),
    ("leave_days", "أيام الإجازة", "int", "sum"),
    ("holidays", "العطل الرسمية", "int", "sum"),
    ("rest", "العطل الأسبوعية", "int", "sum"),
    ("late_count", "مرات التأخير", "int", "sum"),
    ("late", "مجموع التأخير", "minutes", "sum"),
    ("early_count", "مرات الخروج المبكر", "int", "sum"),
    ("early", "مجموع الخروج المبكر", "minutes", "sum"),
    ("missing", "بصمات ناقصة", "int", "sum"),
    ("worked", "مدة العمل", "minutes", "sum"),
    ("required", "المدة المطلوبة", "minutes", "sum"),
    ("overtime", "الإضافي", "minutes", "sum"),
    ("hourly_leave", "الإجازات الزمنية", "minutes", "sum"),
    ("off_day_work", "عمل في أيام العطل", "int", "sum"),
    ("rate", "نسبة الحضور", "percent", "avg"),
]

PUNCH_FIELDS = [
    ("date", "التاريخ", "date", ""),
    ("weekday", "اليوم", "text", ""),
    ("time", "الوقت", "time", ""),
    ("code", "الرقم الوظيفي", "text", ""),
    ("name", "الاسم", "text", ""),
    ("group", "المجموعة", "text", ""),
    ("source", "المصدر", "text", ""),
    ("kind", "النوع", "text", ""),
    ("score", "درجة المطابقة", "float", ""),
    ("liveness", "درجة الحيوية", "float", ""),
    ("original", "الوقت الأصلي", "datetime", ""),
    ("state", "حالة البصمة", "text", ""),
]

LEAVE_FIELDS = [
    ("code", "الرقم الوظيفي", "text", ""),
    ("name", "الاسم", "text", ""),
    ("group", "المجموعة", "text", ""),
    ("type", "نوع الإجازة", "text", ""),
    ("start", "من", "date", ""),
    ("end", "إلى", "date", ""),
    ("start_time", "من الساعة", "time", ""),
    ("end_time", "إلى الساعة", "time", ""),
    ("days", "عدد الأيام", "int", "sum"),
    ("hours", "عدد الساعات", "hours", "sum"),
    ("reference", "رقم الأمر", "text", ""),
    ("note", "ملاحظات", "text", ""),
    ("state", "الحالة", "text", ""),
    ("cancel_reason", "سبب الإلغاء", "text", ""),
]

CORRECTION_FIELDS = [
    ("created_at", "تاريخ التعديل", "datetime", ""),
    ("username", "المستخدم", "text", ""),
    ("code", "الرقم الوظيفي", "text", ""),
    ("name", "الاسم", "text", ""),
    ("action", "الإجراء", "text", ""),
    ("old_ts", "القيمة السابقة", "datetime", ""),
    ("new_ts", "القيمة الجديدة", "datetime", ""),
    ("reason", "السبب", "text", ""),
]

EMPLOYEE_FIELDS = [
    ("code", "الرقم الوظيفي", "text", ""),
    ("name", "الاسم", "text", ""),
    ("job_title", "العنوان الوظيفي", "text", ""),
    ("department", "القسم", "text", ""),
    ("group", "المجموعة", "text", ""),
    ("hire_date", "تاريخ المباشرة", "date", ""),
    ("end_date", "تاريخ ترك الخدمة", "date", ""),
    ("active", "فعّال", "text", ""),
    ("samples", "عينات الوجه", "int", "sum"),
    ("phone", "الهاتف", "text", ""),
    ("notes", "ملاحظات", "text", ""),
]

SOURCES = {
    "daily": ("السجل اليومي (يوم لكل موظف)", DAILY_FIELDS),
    "summary": ("خلاصة لكل موظف", SUMMARY_FIELDS),
    "punches": ("البصمات", PUNCH_FIELDS),
    "leaves": ("الإجازات", LEAVE_FIELDS),
    "corrections": ("التعديلات وأسبابها", CORRECTION_FIELDS),
    "employees": ("بيانات الموظفين", EMPLOYEE_FIELDS),
}

DAILY_STATUS_FILTERS = {
    "present": "حاضر",
    "late": "متأخر",
    "early": "خروج مبكر",
    "absent": "غائب",
    "leave": "إجازة",
    "holiday": "عطلة رسمية",
    "rest": "عطلة أسبوعية",
    "missing": "بصمة ناقصة",
    "corrected": "معدّل يدوياً",
    "overtime": "عمل إضافي",
}

SOURCE_LABELS = {"face": "بصمة وجه", "manual": "يدوية", "pin": "رمز سري", "import": "مستوردة"}
KIND_LABELS = {"auto": "تلقائي", "in": "حضور", "out": "انصراف"}
ACTION_LABELS = {"add": "إضافة", "edit": "تعديل", "delete": "حذف", "restore": "استرجاع"}

PERIODS = {
    "today": "اليوم",
    "yesterday": "أمس",
    "this_week": "هذا الأسبوع",
    "last_week": "الأسبوع الماضي",
    "this_month": "هذا الشهر",
    "last_month": "الشهر الماضي",
    "this_year": "هذه السنة",
    "custom": "فترة محددة",
}


def resolve_period(spec: dict | None, today: date) -> tuple[date, date]:
    spec = spec or {"type": "this_month"}
    kind = spec.get("type", "this_month")
    if kind == "today":
        return today, today
    if kind == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if kind in ("this_week", "last_week"):
        start = today - timedelta(days=(today.weekday() + 1) % 7)  # week starts on Sunday
        if kind == "last_week":
            start -= timedelta(days=7)
        return start, start + timedelta(days=6)
    if kind == "this_month":
        return month_bounds(today.year, today.month)
    if kind == "last_month":
        first = today.replace(day=1) - timedelta(days=1)
        return month_bounds(first.year, first.month)
    if kind == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    return parse_date(spec.get("start")) or today, parse_date(spec.get("end")) or today


# --------------------------------------------------------------------------
def select_employees(db: Database, start: date, end: date, group_ids=None, employee_ids=None,
                     department: str = "", include_inactive: bool = False) -> list[int]:
    rows = db.q("SELECT id, department, active, hire_date, end_date FROM employees")
    ids = []
    member = {}
    if group_ids:
        for r in db.q("SELECT employee_id, group_id, start_date, end_date FROM employee_groups"):
            s, e = parse_date(r["start_date"]), parse_date(r["end_date"])
            if r["group_id"] in group_ids and s <= end and (e is None or e >= start):
                member[r["employee_id"]] = True
    for r in rows:
        if employee_ids and r["id"] not in employee_ids:
            continue
        if group_ids and r["id"] not in member:
            continue
        if department and department.strip() not in (r["department"] or ""):
            continue
        if not include_inactive and not r["active"]:
            hire, gone = parse_date(r["hire_date"]), parse_date(r["end_date"])
            # still include people who left during the period (their days count)
            if not (gone and gone >= start):
                continue
        ids.append(r["id"])
    return ids


def _employee_info(db: Database) -> dict[int, dict]:
    groups = db.groups.names()
    current = {r["employee_id"]: r["group_id"] for r in
               db.q("SELECT employee_id, group_id FROM employee_groups WHERE end_date IS NULL")}
    out = {}
    for r in db.q("SELECT * FROM employees"):
        out[r["id"]] = dict(code=r["code"], name=r["full_name"], department=r["department"],
                            job_title=r["job_title"], group=groups.get(current.get(r["id"]), ""),
                            group_names=groups)
    return out


def day_notes(rec: E.DayRecord) -> str:
    notes = [E.FLAG_LABELS[f] for f in sorted(rec.flags)
             if f in E.FLAG_LABELS and f not in ("at_work",)]
    return "، ".join(notes)


def status_codes(rec: E.DayRecord) -> set[str]:
    codes = {rec.status}
    if rec.counts_present:
        codes.add("present")
    if rec.late:
        codes.add("late")
    if rec.early:
        codes.add("early")
    if "missing_in" in rec.flags or "missing_out" in rec.flags:
        codes.add("missing")
    if "corrected" in rec.flags or "manual" in rec.flags:
        codes.add("corrected")
    if rec.overtime:
        codes.add("overtime")
    return codes


def tone_for(rec: E.DayRecord) -> str:
    if rec.status == E.ABSENT:
        return "bad"
    if rec.status in (E.LEAVE,):
        return "info"
    if rec.status in (E.HOLIDAY, E.REST, E.INACTIVE, E.FUTURE, E.NO_SCHEDULE):
        return "muted"
    if rec.late or rec.early or "missing_in" in rec.flags or "missing_out" in rec.flags:
        return "warn"
    return ""


def daily_rows(db: Database, start: date, end: date, emp_ids: list[int], group_ids=None,
               statuses=None, now: datetime | None = None) -> list[dict]:
    info = _employee_info(db)
    groups = db.groups.names()
    rows = []
    for emp, days in db.compute(emp_ids, start, end, now=now):
        meta = info.get(emp.id, {})
        for rec in days:
            if group_ids and rec.group_id not in group_ids:
                continue
            if statuses and not (status_codes(rec) & set(statuses)):
                continue
            rows.append({
                "employee_id": emp.id,
                "date": rec.date,
                "weekday": weekday_name(rec.date),
                "code": emp.code,
                "name": emp.name,
                "group": groups.get(rec.group_id, ""),
                "department": meta.get("department", ""),
                "job_title": meta.get("job_title", ""),
                "schedule": rec.schedule.name if rec.schedule else "",
                "shift_start": rec.shift.start if rec.shift else None,
                "shift_end": rec.shift.end if rec.shift else None,
                "check_in": rec.check_in,
                "check_out": rec.check_out,
                "status": rec.label,
                "status_code": rec.status,
                "late": rec.late,
                "early": rec.early,
                "worked": rec.worked,
                "overtime": rec.overtime,
                "required": rec.required,
                "hourly_leave": rec.hourly_leave_minutes,
                "leave_type": rec.leave.type.name if rec.leave else "",
                "holiday": rec.holiday.name if rec.holiday else "",
                "punch_count": len(rec.punches),
                "notes": day_notes(rec),
                "_tone": tone_for(rec),
                "_record": rec,
            })
    return rows


def summary_rows(db: Database, start: date, end: date, emp_ids: list[int], group_ids=None,
                 now: datetime | None = None) -> tuple[list[dict], list[tuple]]:
    """Per-employee totals.  Also returns extra per-leave-type fields."""
    info = _employee_info(db)
    types = db.leaves.types()
    extra = [(f"leave:{t['id']}", t["name"], "int", "sum") for t in types if t["unit"] == "day"]
    rows = []
    for emp, days in db.compute(emp_ids, start, end, now=now):
        if group_ids:
            days = [d for d in days if d.group_id in group_ids]
        s = E.summarize(days)
        meta = info.get(emp.id, {})
        row = {
            "employee_id": emp.id, "code": emp.code, "name": emp.name,
            "group": meta.get("group", ""), "department": meta.get("department", ""),
            "job_title": meta.get("job_title", ""),
            "workdays": s.workdays, "present": s.present_days, "absent": s.absent_days,
            "leave_days": s.leave_days, "holidays": s.holiday_days, "rest": s.rest_days,
            "late_count": s.late_count, "late": s.late_minutes, "early_count": s.early_count,
            "early": s.early_minutes, "missing": s.missing_count, "worked": s.worked_minutes,
            "required": s.required_minutes, "overtime": s.overtime_minutes,
            "hourly_leave": s.hourly_leave_minutes, "off_day_work": s.off_day_work_days,
            "rate": s.attendance_rate, "_summary": s,
            "_tones": {k: t for k, t, on in (("absent", "bad", s.absent_days),
                                             ("late", "warn", s.late_minutes),
                                             ("missing", "warn", s.missing_count)) if on},
        }
        for t in types:
            row[f"leave:{t['id']}"] = s.leave_by_type.get(t["name"], 0)
        rows.append(row)
    return rows, extra


def punch_rows(db: Database, start: date, end: date, emp_ids: list[int],
               include_deleted: bool = False) -> list[dict]:
    info = _employee_info(db)
    wanted = set(emp_ids)
    rows = []
    for p in db.punches.list(None, start, end, include_deleted=include_deleted):
        if p["employee_id"] not in wanted:
            continue
        ts = parse_dt(p["ts"])
        state = "محذوفة" if p["deleted"] else ("معدّلة" if p["corrections"] else "")
        rows.append({
            "employee_id": p["employee_id"], "punch_id": p["id"],
            "date": ts.date(), "weekday": weekday_name(ts.date()), "time": ts,
            "code": p["code"], "name": p["full_name"],
            "group": info.get(p["employee_id"], {}).get("group", ""),
            "source": SOURCE_LABELS.get(p["source"], p["source"]),
            "kind": KIND_LABELS.get(p["kind"], p["kind"]),
            "score": p["score"], "liveness": p["liveness"],
            "original": parse_dt(p["original_ts"]) if p["original_ts"] != p["ts"] else None,
            "state": state, "snapshot": p["snapshot"],
            "_tone": "muted" if p["deleted"] else ("warn" if p["corrections"] else ""),
        })
    return rows


def leave_rows(db: Database, start: date, end: date, emp_ids: list[int], type_id=None,
               include_cancelled: bool = False) -> list[dict]:
    info = _employee_info(db)
    wanted = set(emp_ids)
    org = db.load_org()
    rows = []
    leaves = db.leaves.list(None, start, end, type_id, include_cancelled)
    emps = {e.id: e for e in db.load_employees(sorted({l["employee_id"] for l in leaves} & wanted),
                                               start, end)}
    for lv in leaves:
        if lv["employee_id"] not in wanted:
            continue
        s, e = parse_date(lv["start_date"]), parse_date(lv["end_date"])
        days = hours = None
        if lv["unit"] == "day":
            emp = emps.get(lv["employee_id"])
            if emp is not None and not lv["cancelled"]:
                recs = E.compute_days(emp, org, max(s, start), min(e, end))
                days = sum(1 for r in recs if r.status == E.LEAVE and r.leave and r.leave.id == lv["id"])
            else:
                days = (min(e, end) - max(s, start)).days + 1
        else:
            st, en = lv["start_time"], lv["end_time"]
            h1, m1 = map(int, st.split(":"))
            h2, m2 = map(int, en.split(":"))
            hours = ((h2 * 60 + m2) - (h1 * 60 + m1)) / 60
        rows.append({
            "employee_id": lv["employee_id"], "leave_id": lv["id"],
            "code": lv["code"], "name": lv["full_name"],
            "group": info.get(lv["employee_id"], {}).get("group", ""),
            "type": lv["type_name"], "start": s, "end": e,
            "start_time": lv["start_time"] or "", "end_time": lv["end_time"] or "",
            "days": days, "hours": hours, "reference": lv["reference"], "note": lv["note"],
            "state": "ملغاة" if lv["cancelled"] else "فعّالة",
            "cancel_reason": lv["cancel_reason"] or "",
            "_tone": "muted" if lv["cancelled"] else "",
        })
    return rows


def correction_rows(db: Database, start: date, end: date, emp_ids: list[int]) -> list[dict]:
    wanted = set(emp_ids)
    rows = []
    for c in db.punches.corrections(None, start, end):
        if c["employee_id"] not in wanted:
            continue
        rows.append({
            "employee_id": c["employee_id"],
            "created_at": parse_dt(c["created_at"]), "username": c["username"],
            "code": c["code"], "name": c["full_name"],
            "action": ACTION_LABELS.get(c["action"], c["action"]),
            "old_ts": parse_dt(c["old_ts"]) if c["old_ts"] else None,
            "new_ts": parse_dt(c["new_ts"]) if c["new_ts"] else None,
            "reason": c["reason"],
            "_tone": "bad" if c["action"] == "delete" else "",
        })
    return rows


def employee_rows(db: Database, emp_ids: list[int]) -> list[dict]:
    wanted = set(emp_ids)
    rows = []
    for e in db.employees.list(active=None):
        if e["id"] not in wanted:
            continue
        rows.append({
            "employee_id": e["id"], "code": e["code"], "name": e["full_name"],
            "job_title": e["job_title"], "department": e["department"],
            "group": e["group_name"] or "", "hire_date": parse_date(e["hire_date"]),
            "end_date": parse_date(e["end_date"]), "active": "نعم" if e["active"] else "لا",
            "samples": e["samples"], "phone": e["phone"], "notes": e["notes"],
            "_tone": "" if e["active"] else "muted",
        })
    return rows
