"""Ready-made reports."""

from __future__ import annotations

from datetime import date

from ..core import engine as E
from ..core.timeutil import (WEEKDAYS_SHORT, daterange, digits, fmt_date, fmt_date_long,
                             fmt_month, month_bounds)
from ..db.store import EVENT_LABELS, Database
from . import sources as S
from .model import Column, Report, Section, compute_totals

# id, title, description, period kind ("day" | "period" | "month" | "year")
CATALOG = [
    ("daily", "تقرير الحضور اليومي",
     "حالة كل موظف في يوم محدد: وقت الحضور والانصراف والتأخير والغياب.", "day"),
    ("monthly_summary", "الخلاصة الشهرية",
     "مجاميع الحضور والغياب والإجازات والتأخير والإضافي لكل موظف خلال فترة.", "period"),
    ("monthly_grid", "سجل الدوام الشهري",
     "جدول بأسماء الموظفين وأيام الشهر مع رمز الحالة لكل يوم (يطبع بالعرض).", "month"),
    ("timesheet", "كشف دوام تفصيلي",
     "تفاصيل كل يوم لموظف أو أكثر: الحضور والانصراف ومدة العمل والملاحظات.", "period"),
    ("lateness", "تقرير التأخير والخروج المبكر",
     "الأيام التي تأخر فيها الموظف أو خرج مبكراً مع المجاميع.", "period"),
    ("absence", "تقرير الغياب", "أيام الغياب دون إجازة لكل موظف.", "period"),
    ("missing", "تقرير البصمات الناقصة",
     "الأيام التي ينقصها تسجيل حضور أو انصراف (لمراجعتها وتصحيحها).", "period"),
    ("overtime", "تقرير العمل الإضافي", "العمل بعد نهاية الدوام وفي أيام العطل.", "period"),
    ("leaves", "تقرير الإجازات", "الإجازات المسجلة خلال الفترة بأنواعها.", "period"),
    ("leave_balance", "أرصدة الإجازات", "المستخدم والمتبقي من كل نوع إجازة خلال السنة.", "year"),
    ("corrections", "سجل التعديلات وأسبابها",
     "كل إضافة أو تعديل أو حذف لبصمة، مع اسم المستخدم والسبب المكتوب.", "period"),
    ("punches", "سجل البصمات", "جميع البصمات المسجلة كما هي مع درجة المطابقة.", "period"),
    ("security", "أحداث الأمان",
     "محاولات الانتحال، الوجوه غير المعروفة، الدخول الفاشل، وتغيير ساعة الجهاز.", "period"),
]
TITLES = {c[0]: c[1] for c in CATALOG}

GRID_SYMBOLS = [
    ("ح", "حاضر"), ("ت", "متأخر"), ("خ", "خروج مبكر"), ("ن", "بصمة ناقصة"), ("غ", "غائب"),
    ("ع", "عطلة رسمية"), ("·", "عطلة أسبوعية"),
]


def cols(fields, keys, overrides=None):
    by_key = {f[0]: f for f in fields}
    out = []
    for k in keys:
        key, title, kind, total = by_key[k]
        c = Column(key, title, kind, total=total)
        if overrides and k in overrides:
            for attr, val in overrides[k].items():
                setattr(c, attr, val)
        out.append(c)
    return out


def period_text(start: date, end: date) -> str:
    if start == end:
        return fmt_date_long(start)
    first, last = month_bounds(start.year, start.month)
    if start == first and end == last:
        return f"شهر {fmt_month(start.year, start.month)}"
    return f"للفترة من {fmt_date(start)} إلى {fmt_date(end)}"


def _scope(db: Database, p: dict, start: date, end: date) -> list[int]:
    return S.select_employees(db, start, end, p.get("group_ids"), p.get("employee_ids"),
                              p.get("department", ""), p.get("include_inactive", False))


def _signatures(db: Database) -> list[str]:
    return db.settings.get("reports.signatures") or []


def _group_sections(rows, key, columns, sort_key=None) -> list[Section]:
    sections: dict = {}
    for r in rows:
        sections.setdefault(r.get(key) or "—", []).append(r)
    out = []
    for title, items in sections.items():
        if sort_key:
            items.sort(key=sort_key)
        out.append(Section(str(title), items, compute_totals(columns, items)))
    return out


def build(db: Database, report_id: str, p: dict) -> Report:
    fn = globals()[f"report_{report_id}"]
    rep = fn(db, p)
    rep.meta.setdefault("org", db.settings.get("org.name"))
    rep.meta.setdefault("org_subtitle", db.settings.get("org.subtitle"))
    rep.meta.setdefault("generated", db.now())
    rep.meta.setdefault("user", (db.user.full_name or db.user.username) if db.user else "")
    if not rep.signatures:
        rep.signatures = _signatures(db)
    return rep


# --------------------------------------------------------------------------
def report_daily(db: Database, p: dict) -> Report:
    day = p.get("day") or p.get("start") or db.now().date()
    ids = _scope(db, p, day, day)
    rows = S.daily_rows(db, day, day, ids, p.get("group_ids"), p.get("statuses"), now=db.now())
    rows.sort(key=lambda r: (r["group"], r["name"]))
    columns = cols(S.DAILY_FIELDS, ["code", "name", "shift_start", "check_in", "check_out",
                                    "late", "early", "worked", "status", "notes"],
                   {"name": {"width": 2.2}, "notes": {"width": 1.8}, "status": {"width": 1.4}})
    counts: dict[str, int] = {}
    for r in rows:
        rec = r["_record"]
        key = "حاضر" if rec.counts_present else rec.label if rec.status != E.LEAVE else "إجازة"
        if rec.status == E.PRESENT and rec.late:
            counts["متأخر"] = counts.get("متأخر", 0) + 1
        counts[key] = counts.get(key, 0) + 1
    summary = "، ".join(f"{k}: {digits(v)}" for k, v in counts.items())
    rep = Report(TITLES["daily"], columns, subtitle=period_text(day, day), rows=rows,
                 notes=[f"العدد الكلي: {digits(len(rows))} — {summary}"] if rows else [])
    rep.totals = compute_totals(columns, rows)
    if len({r["group"] for r in rows}) > 1:
        rep.sections = _group_sections(rows, "group", columns)
    return rep


def report_monthly_summary(db: Database, p: dict) -> Report:
    start, end = p["start"], p["end"]
    ids = _scope(db, p, start, end)
    rows, extra = S.summary_rows(db, start, end, ids, p.get("group_ids"), now=db.now())
    rows.sort(key=lambda r: (r["group"], r["name"]))
    fields = S.SUMMARY_FIELDS + extra
    keys = ["code", "name", "workdays", "present", "absent", "leave_days", "late_count", "late",
            "early", "missing", "worked", "overtime", "rate"]
    columns = cols(fields, keys, {"name": {"width": 2.2}})
    rep = Report(TITLES["monthly_summary"], columns, rows, subtitle=period_text(start, end),
                 landscape=True)
    rep.totals = compute_totals(columns, rows)
    if p.get("by_group", True) and len({r["group"] for r in rows}) > 1:
        rep.sections = _group_sections(rows, "group", columns)
    return rep


def report_monthly_grid(db: Database, p: dict) -> Report:
    year, month = p.get("year"), p.get("month")
    if not year:
        start = p.get("start") or db.now().date()
        year, month = start.year, start.month
    start, end = month_bounds(year, month)
    ids = _scope(db, p, start, end)
    types = db.leaves.types_engine()
    columns = [Column("code", "الرقم", "text", 1.1), Column("name", "الاسم", "text", 3.0)]
    for d in daterange(start, end):
        columns.append(Column(f"d{d.day}", f"{digits(d.day)}\n{WEEKDAYS_SHORT[d.weekday()]}",
                              "text", 0.55))
    columns += [Column("present", "حضور", "int", 0.8, "sum"), Column("absent", "غياب", "int", 0.8, "sum"),
                Column("leave", "إجازة", "int", 0.8, "sum"), Column("late", "تأخير", "minutes", 1.0, "sum")]
    rows = []
    for emp, days in db.compute(ids, start, end, now=db.now()):
        if p.get("group_ids"):
            days = [d for d in days if d.group_id in p["group_ids"]] or days
        row = {"code": emp.code, "name": emp.name, "_tones": {}}
        for rec in days:
            sym, tone = _grid_symbol(rec)
            row[f"d{rec.date.day}"] = sym
            if tone:
                row["_tones"][f"d{rec.date.day}"] = tone
        s = E.summarize(days)
        row.update(present=s.present_days, absent=s.absent_days, leave=s.leave_days, late=s.late_minutes)
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    legend = "  ".join(f"{s}: {t}" for s, t in GRID_SYMBOLS)
    leave_legend = "  ".join(f"{t.short}: {t.name}" for t in types.values() if t.unit == "day")
    rep = Report(TITLES["monthly_grid"], columns, rows, subtitle=f"شهر {fmt_month(year, month)}",
                 landscape=True, notes=[f"الرموز — {legend}  {leave_legend}"], font_scale=0.85)
    rep.totals = compute_totals(columns, rows)
    return rep


def _grid_symbol(rec: E.DayRecord) -> tuple[str, str]:
    if rec.status in (E.INACTIVE, E.FUTURE, E.PENDING):
        return "", ""
    if rec.status == E.ABSENT:
        return "غ", "bad"
    if rec.status == E.LEAVE and rec.leave:
        return rec.leave.type.short or "ج", "info"
    if rec.status == E.HOLIDAY:
        return "ع", "muted"
    if rec.status == E.REST:
        return ("ح" if rec.punches else "·"), "muted"
    if rec.status == E.NO_SCHEDULE:
        return ("ح" if rec.punches else ""), ""
    if "missing_in" in rec.flags or "missing_out" in rec.flags:
        return "ن", "warn"
    if rec.late:
        return "ت", "warn"
    if rec.early:
        return "خ", "warn"
    return "ح", ""


def report_timesheet(db: Database, p: dict) -> Report:
    start, end = p["start"], p["end"]
    ids = _scope(db, p, start, end)
    rows = S.daily_rows(db, start, end, ids, p.get("group_ids"), now=db.now())
    columns = cols(S.DAILY_FIELDS, ["date", "weekday", "shift_start", "shift_end", "check_in",
                                    "check_out", "late", "early", "worked", "overtime", "status",
                                    "notes"], {"notes": {"width": 2.0}, "status": {"width": 1.5}})
    rep = Report(TITLES["timesheet"], columns, subtitle=period_text(start, end))
    sections = []
    for emp_id in ids:
        mine = [r for r in rows if r["employee_id"] == emp_id]
        if not mine:
            continue
        s = E.summarize([r["_record"] for r in mine])
        title = f"{mine[0]['name']} — الرقم الوظيفي {digits(mine[0]['code'])} — {mine[0]['group']}"
        sec = Section(title, mine, compute_totals(columns, mine))
        sec.subtotal["status"] = (f"حضور {digits(s.present_days)} / غياب {digits(s.absent_days)}"
                                  f" / إجازة {digits(s.leave_days)}")
        sections.append(sec)
    rep.sections = sections
    rep.rows = rows
    rep.page_per_section = p.get("page_per_employee", True)
    return rep


def _daily_filtered(db, p, keep, keys, title, sort=None, overrides=None, landscape=False):
    start, end = p["start"], p["end"]
    ids = _scope(db, p, start, end)
    rows = [r for r in S.daily_rows(db, start, end, ids, p.get("group_ids"), now=db.now()) if keep(r)]
    columns = cols(S.DAILY_FIELDS, keys, overrides)
    rep = Report(title, columns, rows, subtitle=period_text(start, end), landscape=landscape)
    rep.totals = compute_totals(columns, rows)
    rep.sections = _group_sections(rows, "name", columns, sort)
    return rep


def report_lateness(db: Database, p: dict) -> Report:
    return _daily_filtered(
        db, p, lambda r: r["late"] or r["early"],
        ["date", "weekday", "shift_start", "check_in", "late", "shift_end", "check_out", "early",
         "notes"], TITLES["lateness"], overrides={"notes": {"width": 2.0}})


def report_absence(db: Database, p: dict) -> Report:
    return _daily_filtered(db, p, lambda r: r["status_code"] == E.ABSENT,
                           ["date", "weekday", "schedule", "shift_start", "shift_end"],
                           TITLES["absence"])


def report_missing(db: Database, p: dict) -> Report:
    rep = _daily_filtered(
        db, p, lambda r: "missing_in" in r["_record"].flags or "missing_out" in r["_record"].flags,
        ["date", "weekday", "shift_start", "shift_end", "check_in", "check_out", "notes"],
        TITLES["missing"], overrides={"notes": {"width": 2.0}})
    rep.notes.append("تُصحَّح هذه الأيام من «السجل الزمني» بإضافة البصمة الناقصة مع كتابة السبب.")
    return rep


def report_overtime(db: Database, p: dict) -> Report:
    return _daily_filtered(db, p, lambda r: r["overtime"],
                           ["date", "weekday", "status", "shift_end", "check_in", "check_out",
                            "worked", "overtime"], TITLES["overtime"])


def report_leaves(db: Database, p: dict) -> Report:
    start, end = p["start"], p["end"]
    ids = _scope(db, p, start, end)
    rows = S.leave_rows(db, start, end, ids, p.get("type_id"), p.get("include_cancelled", False))
    columns = cols(S.LEAVE_FIELDS, ["code", "name", "type", "start", "end", "start_time",
                                    "end_time", "days", "hours", "reference", "note", "state"],
                   {"name": {"width": 2.0}, "note": {"width": 1.8}})
    rep = Report(TITLES["leaves"], columns, rows, subtitle=period_text(start, end), landscape=True)
    rep.totals = compute_totals(columns, rows)
    return rep


def report_leave_balance(db: Database, p: dict) -> Report:
    year = p.get("year") or (p.get("start") or db.now().date()).year
    ids = _scope(db, p, date(year, 1, 1), date(year, 12, 31))
    info = {r["id"]: r for r in db.employees.list(active=None)}
    types = [t for t in db.leaves.types() if t["active"]]
    columns = [Column("code", "الرقم الوظيفي", "text", 1.1), Column("name", "الاسم", "text", 2.4)]
    for t in types:
        unit = "ساعة" if t["unit"] == "hour" else "يوم"
        columns.append(Column(f"u{t['id']}", f"{t['name']}\nالمستخدم ({unit})", "hours"))
        if t["quota"] is not None:
            columns.append(Column(f"r{t['id']}", "المتبقي", "hours"))
    rows = []
    for eid in ids:
        bal = {b["type_id"]: b for b in db.leaves.balance(eid, year)}
        row = {"code": info[eid]["code"], "name": info[eid]["full_name"]}
        for t in types:
            b = bal.get(t["id"])
            row[f"u{t['id']}"] = b["used"] if b else 0
            if t["quota"] is not None and b:
                row[f"r{t['id']}"] = b["remaining"]
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    quotas = "، ".join(f"{t['name']}: {digits(int(t['quota']) if t['quota'] == int(t['quota']) else t['quota'])}"
                      f" {'ساعة' if t['unit'] == 'hour' else 'يوم'}/{'شهر' if t['quota_period'] == 'month' else 'سنة'}"
                      for t in types if t["quota"] is not None)
    return Report(TITLES["leave_balance"], columns, rows, subtitle=f"سنة {digits(year)}",
                  landscape=len(columns) > 8, notes=[f"الرصيد السنوي: {quotas}"] if quotas else [])


def report_corrections(db: Database, p: dict) -> Report:
    start, end = p["start"], p["end"]
    ids = _scope(db, p, start, end)
    rows = S.correction_rows(db, start, end, ids)
    columns = cols(S.CORRECTION_FIELDS, ["created_at", "username", "code", "name", "action",
                                         "old_ts", "new_ts", "reason"],
                   {"reason": {"width": 3.0}, "name": {"width": 1.8}, "created_at": {"width": 1.4},
                    "old_ts": {"width": 1.4}, "new_ts": {"width": 1.4}})
    return Report(TITLES["corrections"], columns, rows, subtitle=period_text(start, end),
                  landscape=True,
                  notes=["سجل التعديلات لا يمكن تغييره أو حذفه من داخل البرنامج."])


def report_punches(db: Database, p: dict) -> Report:
    start, end = p["start"], p["end"]
    ids = _scope(db, p, start, end)
    rows = S.punch_rows(db, start, end, ids, p.get("include_deleted", False))
    columns = cols(S.PUNCH_FIELDS, ["date", "weekday", "time", "code", "name", "source", "score",
                                    "liveness", "state"], {"name": {"width": 2.0}})
    return Report(TITLES["punches"], columns, rows, subtitle=period_text(start, end))


def report_security(db: Database, p: dict) -> Report:
    start, end = p["start"], p["end"]
    names = db.employees.names()
    rows = []
    for ev in db.events.list(start, end):
        rows.append({"ts": ev["ts"][:16].replace("-", "/"), "kind": EVENT_LABELS.get(ev["kind"], ev["kind"]),
                     "employee": names.get(ev["employee_id"], ""), "details": ev["details"],
                     "_tone": "bad" if ev["kind"] in ("spoof", "clock_back", "clock_jump") else ""})
    columns = [Column("ts", "الوقت", "text", 1.4), Column("kind", "الحدث", "text", 1.6),
               Column("employee", "الموظف المعني", "text", 1.6), Column("details", "التفاصيل", "text", 3.4)]
    return Report(TITLES["security"], columns, rows, subtitle=period_text(start, end))


def default_params(report_id: str, today: date) -> dict:
    kind = next(c[3] for c in CATALOG if c[0] == report_id)
    if kind == "day":
        return {"day": today, "start": today, "end": today}
    if kind == "year":
        return {"year": today.year, "start": date(today.year, 1, 1), "end": date(today.year, 12, 31)}
    start, end = month_bounds(today.year, today.month)
    return {"start": start, "end": min(end, today) if kind == "period" else end,
            "year": today.year, "month": today.month}
