from datetime import date, datetime, time

import pytest

from hadir.core import engine as E

T = time.fromisoformat


def weekly(sid=1, start="08:00", end="14:00", off=(4, 5), **kw):
    # Python weekday: Friday == 4, Saturday == 5 (Iraqi weekend)
    days = tuple(E.DayRule(False) if i in off else E.DayRule(True, T(start), T(end))
                 for i in range(7))
    return E.Schedule(sid, "صباحي", "weekly", days, **kw)


def org_with(*schedules, group_sched=None, holidays=(), **kw):
    org = E.OrgData(schedules={s.id: s for s in schedules}, holidays=list(holidays), **kw)
    for gid, items in (group_sched or {}).items():
        org.group_assignments[gid] = [E.Assignment(*it) for it in items]
    return org


def emp(punches=(), leaves=(), group=1, **kw):
    ps = [E.Punch(i + 1, datetime.fromisoformat(p) if isinstance(p, str) else p)
          for i, p in enumerate(punches)]
    return E.EmployeeData(7, "موظف", groups=[E.GroupPeriod(group, date(2000, 1, 1))],
                          leaves=list(leaves), punches=ps, **kw)


def one_day(e, org, d, now=None):
    [r] = E.compute_days(e, org, d, d, now=now)
    return r


SUN = date(2026, 10, 25)  # Sunday, a working day
FRI = date(2026, 10, 30)


@pytest.fixture
def org():
    return org_with(weekly(), group_sched={1: [(1, date(2020, 1, 1))]})


def test_on_time_day(org):
    r = one_day(emp(["2026-10-25 07:55:10", "2026-10-25 14:03:00"]), org, SUN)
    assert r.status == E.PRESENT
    assert r.check_in == datetime(2026, 10, 25, 7, 55, 10)
    assert r.check_out == datetime(2026, 10, 25, 14, 3)
    assert (r.late, r.early, r.overtime) == (0, 0, 0)
    assert r.worked == 367 and r.required == 360
    assert r.label == "حاضر"


def test_late_within_and_beyond_grace(org):
    assert one_day(emp(["2026-10-25 08:10:00", "2026-10-25 14:00"]), org, SUN).late == 0
    r = one_day(emp(["2026-10-25 08:11:00", "2026-10-25 14:00"]), org, SUN)
    assert r.late == 11 and r.label == "متأخر"


def test_late_counted_after_grace_option():
    org = org_with(weekly(late_after_grace=True), group_sched={1: [(1, date(2020, 1, 1))]})
    assert one_day(emp(["2026-10-25 08:25", "2026-10-25 14:00"]), org, SUN).late == 15


def test_early_leave_and_overtime(org):
    r = one_day(emp(["2026-10-25 08:00", "2026-10-25 13:20"]), org, SUN)
    assert r.early == 40 and "خروج مبكر" in r.label
    r = one_day(emp(["2026-10-25 08:00", "2026-10-25 15:10"]), org, SUN)
    assert r.overtime == 70 and r.early == 0
    r = one_day(emp(["2026-10-25 08:00", "2026-10-25 14:20"]), org, SUN)
    assert r.overtime == 0  # below the 30 minute minimum


def test_absent_pending_future(org):
    e = emp()
    assert one_day(e, org, SUN).status == E.ABSENT
    now = datetime(2026, 10, 25, 9, 0)
    r = one_day(e, org, SUN, now=now)
    assert r.status == E.PENDING and "overdue" in r.flags
    assert one_day(e, org, SUN, now=datetime(2026, 10, 25, 7, 0)).status == E.PENDING
    assert one_day(e, org, date(2026, 10, 26), now=now).status == E.FUTURE
    assert one_day(e, org, SUN, now=datetime(2026, 10, 25, 14, 0)).status == E.ABSENT


def test_rest_day_and_work_on_rest_day(org):
    assert one_day(emp(), org, FRI).status == E.REST
    r = one_day(emp(["2026-10-30 09:00", "2026-10-30 12:00"]), org, FRI)
    assert r.status == E.REST and r.overtime == 180 and "worked_off_day" in r.flags


def test_missing_out_and_single_punch_heuristic(org):
    r = one_day(emp(["2026-10-25 08:02"]), org, SUN)
    assert r.check_in and r.check_out is None and "missing_out" in r.flags
    r = one_day(emp(["2026-10-25 13:58"]), org, SUN)
    assert r.check_out and r.check_in is None and "missing_in" in r.flags
    # Today, still inside the shift: a lone punch is an arrival, not "missing out".
    r = one_day(emp(["2026-10-25 11:30"]), org, SUN, now=datetime(2026, 10, 25, 11, 31))
    assert r.check_in and "at_work" in r.flags and r.late == 210


def test_explicit_kind_wins(org):
    e = emp()
    e.punches = [E.Punch(1, datetime(2026, 10, 25, 8, 1), kind="out")]
    r = one_day(e, org, SUN)
    assert r.check_out and r.check_in is None


def test_dedupe_and_pairs(org):
    r = one_day(emp(["2026-10-25 08:00:00", "2026-10-25 08:00:40",  # double scan
                     "2026-10-25 10:00", "2026-10-25 10:30", "2026-10-25 14:00"]), org, SUN)
    assert len(r.punches) == 4
    assert r.worked == 120 + 210
    r = one_day(emp(["2026-10-25 08:00", "2026-10-25 10:00", "2026-10-25 14:00"]), org, SUN)
    assert "odd_punches" in r.flags and r.worked == 360


def test_overnight_shift_assignment():
    night = weekly(2, "20:00", "08:00", off=())
    org = org_with(night, group_sched={1: [(2, date(2020, 1, 1))]})
    e = emp(["2026-10-25 19:50", "2026-10-26 08:05", "2026-10-26 20:20", "2026-10-27 07:40"])
    a, b = E.compute_days(e, org, date(2026, 10, 25), date(2026, 10, 26))
    assert (a.check_in, a.check_out) == (datetime(2026, 10, 25, 19, 50), datetime(2026, 10, 26, 8, 5))
    assert a.late == 0 and a.worked == 735
    assert b.late == 20 and b.early == 20


def test_24h_cycle_shift():
    days = (E.DayRule(True, T("08:00"), T("08:00")), E.DayRule(False), E.DayRule(False))
    duty = E.Schedule(3, "مناوبة", "cycle", days, cycle_anchor=date(2026, 10, 1))
    org = org_with(duty, group_sched={1: [(3, date(2020, 1, 1))]})
    e = emp(["2026-10-25 07:58", "2026-10-26 08:03"])
    recs = E.compute_days(e, org, date(2026, 10, 25), date(2026, 10, 27))
    assert recs[0].status == E.PRESENT and recs[0].required == 1440
    assert recs[0].check_out == datetime(2026, 10, 26, 8, 3)
    assert recs[1].status == E.REST and not recs[1].punches
    assert recs[2].status == E.REST


def test_consecutive_24h_shifts_split_at_handover():
    duty = E.Schedule(3, "24", "cycle", (E.DayRule(True, T("08:00"), T("08:00")),),
                      cycle_anchor=date(2026, 10, 1))
    org = org_with(duty, group_sched={1: [(3, date(2020, 1, 1))]})
    e = emp(["2026-10-25 08:00", "2026-10-26 07:55", "2026-10-26 08:06"])
    a, b = E.compute_days(e, org, date(2026, 10, 25), date(2026, 10, 26))
    assert a.check_out == datetime(2026, 10, 26, 7, 55)
    assert b.check_in == datetime(2026, 10, 26, 8, 6)


def test_holiday_applies_to_groups():
    hol = E.Holiday(1, "عيد", date(2026, 10, 25), date(2026, 10, 26), frozenset({2}))
    org = org_with(weekly(), group_sched={1: [(1, date(2020, 1, 1))], 2: [(1, date(2020, 1, 1))]},
                   holidays=[hol])
    assert one_day(emp(group=1), org, SUN).status == E.ABSENT
    r = one_day(emp(group=2), org, SUN)
    assert r.status == E.HOLIDAY and r.label == "عيد" and r.required == 0


def test_full_day_leave_skips_rest_days_unless_calendar():
    annual = E.LeaveType(1, "اعتيادية")
    mat = E.LeaveType(2, "أمومة", calendar_days=True)
    org = org_with(weekly(), group_sched={1: [(1, date(2020, 1, 1))]})
    lv = E.Leave(1, annual, date(2026, 10, 29), date(2026, 11, 1))
    recs = E.compute_days(emp(leaves=[lv]), org, date(2026, 10, 29), date(2026, 11, 1))
    assert [r.status for r in recs] == [E.LEAVE, E.REST, E.REST, E.LEAVE]
    lv2 = E.Leave(2, mat, date(2026, 10, 29), date(2026, 11, 1))
    recs = E.compute_days(emp(leaves=[lv2]), org, date(2026, 10, 29), date(2026, 11, 1))
    assert all(r.status == E.LEAVE for r in recs)
    s = E.summarize(recs)
    assert s.leave_by_type == {"أمومة": 4}


def test_hourly_leave_excuses_lateness_and_early_exit(org):
    hourly = E.LeaveType(3, "زمنية", unit="hour")
    morning = E.Leave(1, hourly, SUN, SUN, T("08:00"), T("10:00"))
    r = one_day(emp(["2026-10-25 10:05", "2026-10-25 14:00"], leaves=[morning]), org, SUN)
    assert r.late == 0 and r.hourly_leave_minutes == 120
    r = one_day(emp(["2026-10-25 10:25", "2026-10-25 14:00"], leaves=[morning]), org, SUN)
    assert r.late == 25
    evening = E.Leave(2, hourly, SUN, SUN, T("12:00"), T("14:00"))
    r = one_day(emp(["2026-10-25 08:00", "2026-10-25 12:00"], leaves=[evening]), org, SUN)
    assert r.early == 0
    whole = E.Leave(3, hourly, SUN, SUN, T("08:00"), T("14:00"))
    assert one_day(emp(leaves=[whole]), org, SUN).status == E.LEAVE


def test_mission_counts_as_present(org):
    mission = E.LeaveType(4, "إيفاد", counts_present=True)
    r = one_day(emp(leaves=[E.Leave(1, mission, SUN, SUN)]), org, SUN)
    assert r.status == E.LEAVE and r.counts_present and r.label == "إيفاد"


def test_group_change_mid_month_uses_dated_assignment():
    morning, evening = weekly(1), weekly(2, "14:00", "20:00")
    org = org_with(morning, evening, group_sched={1: [(1, date(2020, 1, 1))],
                                                  2: [(2, date(2020, 1, 1))]})
    e = emp(["2026-10-25 08:00", "2026-10-25 14:00", "2026-10-26 14:00", "2026-10-26 20:00"])
    e.groups = [E.GroupPeriod(1, date(2020, 1, 1), date(2026, 10, 25)),
                E.GroupPeriod(2, date(2026, 10, 26))]
    a, b = E.compute_days(e, org, SUN, date(2026, 10, 26))
    assert a.schedule.id == 1 and a.late == 0
    assert b.schedule.id == 2 and b.late == 0 and b.check_out == datetime(2026, 10, 26, 20)


def test_schedule_change_from_date_keeps_history():
    old, new = weekly(1), weekly(2, "09:00", "15:00")
    org = org_with(old, new, group_sched={1: [(1, date(2020, 1, 1)), (2, date(2026, 10, 26))]})
    e = emp(["2026-10-25 08:30", "2026-10-25 14:00", "2026-10-26 08:30", "2026-10-26 15:00"])
    a, b = E.compute_days(e, org, SUN, date(2026, 10, 26))
    assert a.late == 30 and b.late == 0


def test_employee_override_assignment(org):
    org.schedules[9] = weekly(9, "10:00", "16:00")
    e = emp(["2026-10-25 10:00", "2026-10-25 16:00"])
    e.assignments = [E.Assignment(9, date(2026, 10, 1), date(2026, 10, 31))]
    r = one_day(e, org, SUN)
    assert r.schedule.id == 9 and r.late == 0 and r.early == 0


def test_inactive_and_no_schedule():
    org = org_with(weekly())
    e = emp(hire_date=date(2026, 10, 26))
    assert one_day(e, org, SUN).status == E.INACTIVE
    e2 = emp(["2026-10-26 08:00", "2026-10-26 12:00"])
    r = one_day(e2, org, date(2026, 10, 26))
    assert r.status == E.NO_SCHEDULE and r.worked == 240


def test_punch_after_midnight_on_rest_day_goes_to_previous_day(org):
    # Thursday shift (window ends 22:00); a 01:30 punch on Friday is before the 04:00 day
    # boundary, so it belongs to Thursday rather than to the Friday rest day.
    thu, fri = date(2026, 10, 29), FRI
    e = emp(["2026-10-29 08:00", "2026-10-30 01:30"])
    a, b = E.compute_days(e, org, thu, fri)
    assert a.check_out == datetime(2026, 10, 30, 1, 30) and not b.punches


def test_summary(org):
    e = emp(["2026-10-25 08:20", "2026-10-25 14:00", "2026-10-26 08:00", "2026-10-26 13:00"])
    recs = E.compute_days(e, org, SUN, date(2026, 10, 31))
    s = E.summarize(recs)
    assert s.workdays == 5 and s.present_days == 2 and s.absent_days == 3
    assert s.rest_days == 2 and s.late_count == 1 and s.late_minutes == 20
    assert s.early_minutes == 60 and s.attendance_rate == pytest.approx(0.4)


def test_classify_new_punch(org):
    e = emp()
    kind, rec = E.classify_new_punch(e, org, datetime(2026, 10, 25, 7, 58))
    assert kind == "in" and rec.date == SUN
    e = emp(["2026-10-25 07:58"])
    kind, rec = E.classify_new_punch(e, org, datetime(2026, 10, 25, 14, 2))
    assert kind == "out" and rec.check_in == datetime(2026, 10, 25, 7, 58)
    # Forgot to check in: an after-shift punch is a check-out.
    kind, _ = E.classify_new_punch(emp(), org, datetime(2026, 10, 25, 14, 5))
    assert kind == "out"
