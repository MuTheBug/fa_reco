"""Demonstration data (used by tests, screenshots and the «بيانات تجريبية» option)."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from .core.timeutil import daterange
from .db.seed import seed_defaults
from .db.store import Database

FIRST = ["أحمد", "محمد", "علي", "حسين", "زينب", "فاطمة", "مريم", "سارة", "عمر", "مصطفى", "كرار",
         "حيدر", "نور", "رقية", "هدى", "عبد الله", "يوسف", "إبراهيم", "سجاد", "منتظر", "آية",
         "بنين", "دعاء", "مرتضى", "ياسر", "رسل"]
FATHER = ["جاسم", "كاظم", "عباس", "صالح", "حسن", "محمود", "خالد", "عبد الرزاق", "رحيم", "ناصر",
          "فاضل", "ستار"]
FAMILY = ["الربيعي", "الجبوري", "العبيدي", "التميمي", "الساعدي", "الموسوي", "الحسيني", "الخفاجي",
          "الشمري", "الزبيدي", "الدليمي", "الكعبي"]
JOBS = ["مهندس", "محاسب", "ملاحظ", "مبرمج", "معاون إداري", "كاتب", "مدقق", "حارس", "سائق",
        "موظف خدمة"]


def populate(db: Database, today: date, employees: int = 24, days: int = 35, seed: int = 7) -> None:
    rnd = random.Random(seed)
    seed_defaults(db)
    with db.tx():
        base_group = db.scalar("SELECT id FROM emp_groups ORDER BY id")
        db.groups.save("الموظفون", "الدوام الصباحي الاعتيادي", base_group)
        evening = db.schedules.save(dict(
            name="الدوام المسائي", kind="weekly", grace_late=10,
            days=[(True, "14:00", "20:00")] * 4 + [(False, None, None)] * 2 + [(True, "14:00", "20:00")]))
        duty = db.schedules.save(dict(
            name="مناوبة ٢٤ ساعة", kind="cycle", cycle_anchor=(today - timedelta(days=60)).isoformat(),
            grace_late=15, window_before=180, window_after=360,
            days=[(True, "08:00", "08:00"), (False, None, None), (False, None, None)]))
        g_eve = db.groups.save("الشعبة المسائية", "دوام مسائي")
        db.groups.assign_schedule(g_eve, evening, "2000-01-01")
        g_duty = db.groups.save("المناوبون", "حراسة وخدمات بنظام ٢٤/٤٨")
        db.groups.assign_schedule(g_duty, duty, "2000-01-01")
        n_side = min(3, employees // 4)
        plan = [base_group] * (employees - 2 * n_side) + [g_eve] * n_side + [g_duty] * n_side
        ids = []
        start = today - timedelta(days=days)
        for i in range(employees):
            name = f"{rnd.choice(FIRST)} {rnd.choice(FATHER)} {rnd.choice(FAMILY)}"
            eid = db.employees.save(dict(
                code=str(1001 + i), full_name=name, job_title=rnd.choice(JOBS),
                department=rnd.choice(["الإدارة", "الحسابات", "الشؤون الفنية", "الخدمات"]),
                hire_date=(start - timedelta(days=400)).isoformat(), group_id=plan[i]),
                group_effective="2000-01-01")
            ids.append(eid)
        def pick(i):
            return ids[i % len(ids)]

        holiday = start + timedelta(days=9)
        db.holidays.save("عطلة رسمية", holiday, holiday)
        types = {t["name"]: t["id"] for t in db.leaves.types()}
        db.leaves.add(pick(2), types["إجازة اعتيادية"], start + timedelta(days=3), start + timedelta(days=6),
                      reference="أمر إداري ٤٥")
        db.leaves.add(pick(5), types["إجازة مرضية"], start + timedelta(days=15), start + timedelta(days=16))
        db.leaves.add(pick(7), types["واجب رسمي / إيفاد"], start + timedelta(days=20), start + timedelta(days=22))
        hourly_day = start + timedelta(days=12)
        while hourly_day.weekday() in (4, 5):
            hourly_day += timedelta(days=1)
        db.leaves.add(pick(1), types["إجازة زمنية"], hourly_day, None, "08:00", "10:00")

    org = db.load_org()
    emps = {e.id: e for e in db.load_employees(ids, start, today)}
    with db.tx():
        for eid in ids:
            emp = emps[eid]
            habit = rnd.choice([-12, -8, -5, -3, 0, 3, 8])  # minutes vs. shift start
            for d in daterange(start, today - timedelta(days=1)):
                gid = emp.group_on(d)
                sched = org.schedule_for(emp, d, gid)
                shift = sched.shift_for(d) if sched else None
                if shift is None or d == holiday:
                    continue
                if any(lv.start <= d <= lv.end and not lv.hourly for lv in emp.leaves):
                    continue
                r = rnd.random()
                if r < 0.03:
                    continue  # absent
                arrive = shift.start + timedelta(minutes=habit + rnd.gauss(0, 6))
                if rnd.random() < 0.08:
                    arrive += timedelta(minutes=rnd.randint(15, 70))
                if d == hourly_day and eid == pick(1):
                    arrive = shift.start + timedelta(minutes=118)
                leave = shift.end + timedelta(minutes=rnd.gauss(4, 5))
                if rnd.random() < 0.05:
                    leave -= timedelta(minutes=rnd.randint(20, 90))
                if rnd.random() < 0.07:
                    leave += timedelta(minutes=rnd.randint(40, 120))
                arrive += timedelta(seconds=rnd.randint(0, 59))
                leave += timedelta(seconds=rnd.randint(0, 59))
                db.punches.add_face(eid, arrive.replace(microsecond=0), score=round(rnd.uniform(0.62, 0.9), 3),
                                    liveness=round(rnd.uniform(0.85, 1.0), 3))
                if r > 0.98:
                    continue  # forgot to punch out
                db.punches.add_face(eid, leave.replace(microsecond=0), score=round(rnd.uniform(0.62, 0.9), 3),
                                    liveness=round(rnd.uniform(0.85, 1.0), 3))
        # a few corrections with written reasons
        sample = db.punches.list(pick(0), start, today)
        if sample:
            p = sample[0]
            ts = datetime.fromisoformat(p["ts"])
            db.punches.edit(p["id"], ts.replace(hour=7, minute=58, second=0), "auto",
                            "عطل في الكاميرا صباحاً — أُثبت الحضور بشهادة مسؤول الشعبة")
        miss_day = today - timedelta(days=4)
        while miss_day.weekday() in (4, 5):
            miss_day -= timedelta(days=1)
        db.punches.add_manual(pick(3), datetime.combine(miss_day, datetime.min.time()).replace(hour=14, minute=5),
                              "out", "نسي تسجيل الانصراف — بموافقة المدير المباشر")


def demo_database(path, today: date | None = None, **kw) -> Database:
    today = today or date.today()
    db = Database(path, clock=lambda: datetime.combine(today, datetime.min.time()).replace(hour=10, minute=30))
    if not db.users.count():
        db.users.create("admin", "مدير النظام", "admin123", "admin")
    db.users.authenticate("admin", "admin123")
    db.settings.set("org.name", "دائرة تجريبية")
    db.settings.set("org.subtitle", "قسم الموارد البشرية")
    populate(db, today, **kw)
    return db
