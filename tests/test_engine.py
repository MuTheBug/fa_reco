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


# ---------------------------------------------------------------- strict rules
# The department's rules: on time until 08:30, late until 09:00, absent after; exit from 14:45,
# leaving earlier is an absence, a missing punch is an absence, exit punches until 17:00.
def strict(**kw):
    rules = dict(grace_late=30, absent_after=60, exit_until=T("17:00"), early_absent=True,
                 missing_absent=True)
    rules.update(kw)
    return weekly(end="14:45", **rules)


@pytest.fixture
def strict_org():
    return org_with(strict(), group_sched={1: [(1, date(2020, 1, 1))]})


def day(*punches):
    return emp([f"2026-10-25 {p}" for p in punches])


def test_strict_on_time_late_and_absent_after_cutoff(strict_org):
    r = one_day(day("08:29", "14:50"), strict_org, SUN)
    assert (r.status, r.late) == (E.PRESENT, 0)
    r = one_day(day("08:40", "14:50"), strict_org, SUN)
    assert (r.status, r.late) == (E.PRESENT, 40)
    r = one_day(day("09:00", "14:50"), strict_org, SUN)
    assert (r.status, r.late) == (E.PRESENT, 60)  # 09:00 itself is still late, not absent
    r = one_day(day("09:00:30", "14:50"), strict_org, SUN)
    assert r.status == E.ABSENT and "absent_late" in r.flags and r.late == 0
    assert r.label == "غائب (حضر بعد وقت الغياب)"
    assert r.check_in == datetime(2026, 10, 25, 9, 0, 30)  # the punches stay visible


def test_strict_exit_before_exit_time_is_absence_unless_on_leave(strict_org):
    r = one_day(day("08:00", "14:30"), strict_org, SUN)
    assert r.status == E.ABSENT and "absent_early" in r.flags and r.early == 0
    assert r.label == "غائب (خرج قبل وقت الخروج)"
    assert one_day(day("08:00", "14:45"), strict_org, SUN).status == E.PRESENT
    hourly = E.LeaveType(3, "زمنية", unit="hour")
    e = emp(["2026-10-25 08:00", "2026-10-25 13:00"],
            leaves=[E.Leave(1, hourly, SUN, SUN, T("13:00"), T("14:45"))])
    assert one_day(e, strict_org, SUN).status == E.PRESENT
    # Leaving early then coming back: the last punch counts.
    assert one_day(day("08:00", "11:00", "12:00", "14:50"), strict_org, SUN).status == E.PRESENT


def test_strict_missing_exit_is_final_only_after_last_exit_time(strict_org):
    e = day("08:05")
    r = one_day(e, strict_org, SUN, now=datetime(2026, 10, 25, 16, 30))
    assert r.status == E.PRESENT and "at_work" in r.flags
    r = one_day(e, strict_org, SUN, now=datetime(2026, 10, 25, 17, 1))
    assert r.status == E.ABSENT and "missing_out" in r.flags
    assert r.label == "غائب (لم يسجّل انصرافاً)"
    assert one_day(e, strict_org, SUN).status == E.ABSENT


def test_strict_missing_check_in_is_absence(strict_org):
    r = one_day(day("15:00"), strict_org, SUN)  # forgot to punch in, punched out only
    assert r.check_out is not None and r.status == E.ABSENT and "missing_in" in r.flags


def test_strict_no_show_becomes_absent_after_cutoff(strict_org):
    e = emp()
    r = one_day(e, strict_org, SUN, now=datetime(2026, 10, 25, 8, 45))
    assert r.status == E.PENDING and "overdue" in r.flags
    assert one_day(e, strict_org, SUN, now=datetime(2026, 10, 25, 9, 1)).status == E.ABSENT


def test_strict_morning_hourly_leave_moves_the_cutoff(strict_org):
    hourly = E.LeaveType(3, "زمنية", unit="hour")
    lv = [E.Leave(1, hourly, SUN, SUN, T("08:00"), T("10:00"))]
    r = one_day(emp(["2026-10-25 10:20", "2026-10-25 14:50"], leaves=lv), strict_org, SUN)
    assert (r.status, r.late) == (E.PRESENT, 0)
    r = one_day(emp(["2026-10-25 11:05", "2026-10-25 14:50"], leaves=lv), strict_org, SUN)
    assert r.status == E.ABSENT and "absent_late" in r.flags


def test_strict_custom_weekday_times():
    s = strict()
    days = list(s.days)
    days[3] = E.DayRule(True, T("08:00"), T("13:00"))  # Thursday ends early
    s.days = tuple(days)
    org = org_with(s, group_sched={1: [(1, date(2020, 1, 1))]})
    thu = date(2026, 10, 29)
    ok = emp(["2026-10-29 08:10", "2026-10-29 13:05"])
    assert one_day(ok, org, thu).status == E.PRESENT
    early = emp(["2026-10-29 08:10", "2026-10-29 12:50"])
    assert one_day(early, org, thu).status == E.ABSENT


def test_strict_full_day_leave_wins(strict_org):
    annual = E.LeaveType(1, "اعتيادية")
    r = one_day(emp(leaves=[E.Leave(1, annual, SUN, SUN)]), strict_org, SUN)
    assert r.status == E.LEAVE


def test_exit_deadline_and_punch_window():
    s = strict(window_after=60)
    shift = s.shift_for(SUN)
    assert s.exit_deadline(shift) == datetime(2026, 10, 25, 17, 0)
    org = org_with(s, group_sched={1: [(1, date(2020, 1, 1))]})
    r = one_day(day("08:00", "16:30"), org, SUN)  # outside window_after, inside the deadline
    assert r.check_out == datetime(2026, 10, 25, 16, 30) and r.status == E.PRESENT
    night = E.Schedule(2, "ليلي", "weekly", tuple(E.DayRule(True, T("20:00"), T("08:00")) for _ in range(7)),
                       exit_until=T("10:00"))
    assert night.exit_deadline(night.shift_for(SUN)) == datetime(2026, 10, 26, 10, 0)


def test_rules_off_keep_the_old_behaviour(org):
    r = one_day(emp(["2026-10-25 09:30", "2026-10-25 13:00"]), org, SUN)
    assert r.status == E.PRESENT and r.late == 90 and r.early == 60
    r = one_day(emp(["2026-10-25 08:00"]), org, SUN)
    assert r.status == E.PRESENT and "missing_out" in r.flags


# ---------------------------------------------------------------- rotations
GUARD_RULES = dict(grace_late=30, absent_after=60, exit_until=T("10:00"), early_absent=True,
                   missing_absent=True)


def weekly_duty(anchor=date(2026, 10, 25), **kw):
    """Seven days of continuous duty from 08:00, then seven days off."""
    days = tuple([E.DayRule(True, T("08:00"), T("08:00"))] * 7 + [E.DayRule(False)] * 7)
    return E.Schedule(3, "النوبة الأسبوعية", "cycle", days, cycle_anchor=anchor, continuous=True,
                      **(GUARD_RULES | kw))


def guard(punches=(), cycle_start=None, leaves=()):
    e = emp(punches, leaves=leaves)
    e.cycle_start = cycle_start
    return e


def week(e, org, now=None, start=date(2026, 10, 25), end=date(2026, 11, 1)):
    return E.compute_days(e, org, start, end, now=now)


@pytest.fixture
def duty_org():
    return org_with(weekly_duty(), group_sched={1: [(3, date(2020, 1, 1))]})


def test_weekly_duty_needs_one_punch_on_joining_and_one_at_the_end(duty_org):
    days = week(guard(["2026-10-25 07:55", "2026-11-01 08:10"]), duty_org)
    assert [d.status for d in days[:7]] == [E.PRESENT] * 7
    assert days[0].check_in == datetime(2026, 10, 25, 7, 55) and days[0].check_out is None
    assert days[6].check_out == datetime(2026, 11, 1, 8, 10) and days[6].check_in is None
    assert all("on_duty" in d.flags and d.label == "في النوبة" for d in days[1:6])
    assert days[7].status == E.REST and days[7].label == "استراحة"
    assert E.summarize(days).present_days == 7 and E.summarize(days).absent_days == 0


def test_weekly_duty_late_joining_and_early_leaving(duty_org):
    late = week(guard(["2026-10-25 08:40", "2026-11-01 08:00"]), duty_org)
    assert late[0].status == E.PRESENT and late[0].late == 40
    after_cutoff = week(guard(["2026-10-26 10:00", "2026-11-01 08:00"]), duty_org)
    assert [d.status for d in after_cutoff[:3]] == [E.ABSENT, E.ABSENT, E.PRESENT]
    assert "absent_late" in after_cutoff[1].flags and after_cutoff[1].check_in is not None
    early = week(guard(["2026-10-25 08:00", "2026-10-29 12:00"]), duty_org)
    assert [d.status for d in early[:7]] == [E.PRESENT] * 4 + [E.ABSENT] * 3
    assert early[4].check_out == datetime(2026, 10, 29, 12, 0) and "absent_early" in early[4].flags


def test_weekly_duty_missing_exit_and_no_show(duty_org):
    gone = week(guard(["2026-10-25 08:00"]), duty_org)
    assert [d.status for d in gone[:7]] == [E.PRESENT] * 6 + [E.ABSENT]
    assert "missing_out" in gone[6].flags
    nobody = week(guard(), duty_org)
    assert [d.status for d in nobody[:7]] == [E.ABSENT] * 7
    r = week(guard(), duty_org, now=datetime(2026, 10, 25, 8, 45))
    assert r[0].status == E.PENDING and r[1].status == E.FUTURE


def test_weekly_duty_in_progress(duty_org):
    now = datetime(2026, 10, 28, 12, 0)
    days = week(guard(["2026-10-25 08:00"]), duty_org, now=now)
    assert [d.status for d in days[:4]] == [E.PRESENT] * 4 and "at_work" in days[3].flags
    assert [d.status for d in days[4:7]] == [E.FUTURE] * 3
    assert days[3].worked == 240  # 08:00 to 12:00 of the fourth day


def test_weekly_duty_part_of_the_range_and_leave(duty_org):
    annual = E.LeaveType(1, "اعتيادية")
    e = guard(["2026-10-25 08:00", "2026-11-01 08:00"],
              leaves=[E.Leave(1, annual, date(2026, 10, 28), date(2026, 10, 28))])
    middle = E.compute_days(e, duty_org, date(2026, 10, 27), date(2026, 10, 29))
    assert [d.status for d in middle] == [E.PRESENT, E.LEAVE, E.PRESENT]
    assert middle[0].check_in is None and "on_duty" in middle[0].flags


def test_weekly_duty_kiosk_reads_the_end_punch_as_exit(duty_org):
    e = guard(["2026-10-25 08:00"])
    kind, rec = E.classify_new_punch(e, duty_org, datetime(2026, 11, 1, 8, 5))
    assert kind == "out" and rec.date == date(2026, 10, 31)
    kind, _ = E.classify_new_punch(guard(), duty_org, datetime(2026, 10, 25, 7, 58))
    assert kind == "in"


def test_personal_rotation_start(duty_org):
    # This guard's own rotation starts a week later: the first week is rest for him.
    e = guard(["2026-11-01 08:00", "2026-11-08 08:00"], cycle_start=date(2026, 11, 1))
    days = week(e, duty_org, end=date(2026, 11, 8))
    assert [d.status for d in days[:7]] == [E.REST] * 7
    assert [d.status for d in days[7:14]] == [E.PRESENT] * 7
    # A 24-hour duty every third day: teams take turns through their own start days.
    days3 = tuple([E.DayRule(True, T("08:00"), T("08:00")), E.DayRule(False), E.DayRule(False)])
    guards = E.Schedule(4, "دوام الحرس", "cycle", days3, cycle_anchor=date(2026, 10, 25), **GUARD_RULES)
    org = org_with(guards, group_sched={1: [(4, date(2020, 1, 1))]})
    second_team = guard(["2026-10-26 08:00", "2026-10-27 08:05"], cycle_start=date(2026, 10, 26))
    r = E.compute_days(second_team, org, date(2026, 10, 25), date(2026, 10, 27))
    assert [d.status for d in r] == [E.REST, E.PRESENT, E.REST]
    assert r[1].check_out == datetime(2026, 10, 27, 8, 5)


def test_general_holidays_do_not_stop_guard_rotations(duty_org):
    general = E.Holiday(1, "عطلة عامة", date(2026, 10, 27), date(2026, 10, 27))
    duty_org.holidays = [general]
    days = week(guard(["2026-10-25 08:00", "2026-11-01 08:00"]), duty_org)
    assert days[2].status == E.PRESENT  # the guard is on duty through the general holiday
    duty_org.holidays = [E.Holiday(2, "عطلة الحرس", date(2026, 10, 27), date(2026, 10, 27),
                                   frozenset({1}))]
    assert week(guard(["2026-10-25 08:00", "2026-11-01 08:00"]), duty_org)[2].status == E.HOLIDAY


# ---------------------------------------------------------------- exit window, personal times
@pytest.fixture
def window_org():
    # exit punches count from 14:30; 14:30-14:45 is an early exit, 14:45 on is on time
    return org_with(strict(exit_window=15), group_sched={1: [(1, date(2020, 1, 1))]})


def test_exit_window(window_org):
    r = one_day(day("08:00", "14:50"), window_org, SUN)
    assert (r.status, r.early, r.check_out) == (E.PRESENT, 0, datetime(2026, 10, 25, 14, 50))
    r = one_day(day("08:00", "14:35"), window_org, SUN)
    assert (r.status, r.early) == (E.PRESENT, 10) and r.label == "خروج مبكر"
    # Before 14:30 a punch is not an exit: it does not count, and nobody punched out.
    r = one_day(day("08:00", "11:00"), window_org, SUN)
    assert r.check_out is None and "early_punch" in r.flags and r.status == E.ABSENT
    assert "missing_out" in r.flags
    r = one_day(day("08:00", "11:00", "14:46"), window_org, SUN)
    assert r.status == E.PRESENT and r.check_out == datetime(2026, 10, 25, 14, 46) and r.worked == 406
    still_working = one_day(day("08:00", "11:00"), window_org, SUN, now=datetime(2026, 10, 25, 12, 0))
    assert still_working.status == E.PRESENT and "at_work" in still_working.flags


def test_exit_window_and_explicit_exits(window_org):
    # An exit entered by hand as «out» before the window is still an exit: an absence by the rules.
    e = emp(leaves=())
    e.punches = [E.Punch(1, datetime(2026, 10, 25, 8, 0)), E.Punch(2, datetime(2026, 10, 25, 12, 0), "out")]
    r = one_day(e, window_org, SUN)
    assert r.status == E.ABSENT and "absent_early" in r.flags
    # An hourly leave at the end of the day moves the window with it.
    hourly = E.LeaveType(3, "زمنية", unit="hour")
    e = emp(["2026-10-25 08:00", "2026-10-25 12:50"],
            leaves=[E.Leave(1, hourly, SUN, SUN, T("13:00"), T("14:45"))])
    r = one_day(e, window_org, SUN)
    assert (r.status, r.early) == (E.PRESENT, 10)


def test_a_punch_after_leaving_is_not_a_second_exit(window_org):
    # left at 14:50, passed the camera again at 15:50: the day still ends at 14:50 (no overtime)
    r = one_day(day("08:00", "14:50", "15:50"), window_org, SUN)
    assert (r.status, r.check_out, r.overtime, r.worked) == (E.PRESENT, datetime(2026, 10, 25, 14, 50), 0, 410)
    assert "extra_punch" in r.flags
    # an early exit can still be followed by the real one
    r = one_day(day("08:00", "14:35", "14:50"), window_org, SUN)
    assert (r.check_out, r.early) == (datetime(2026, 10, 25, 14, 50), 0) and "extra_punch" not in r.flags


def test_left_at_tells_the_kiosk_the_day_is_over(window_org):
    later = datetime(2026, 10, 25, 15, 50)
    for punches, left in ((("08:00", "14:50"), datetime(2026, 10, 25, 14, 50)),
                          (("08:00", "14:35"), None),  # an early exit can be replaced
                          (("08:00",), None)):
        e = day(*punches)
        _, rec = E.classify_new_punch(e, window_org, later)
        assert E.left_at(e, window_org, rec, later) == left
    # with an hourly leave until the end of the day, leaving at its start ends the day
    hourly = E.LeaveType(3, "زمنية", unit="hour")
    e = emp(["2026-10-25 08:00", "2026-10-25 13:00"], leaves=[E.Leave(1, hourly, SUN, SUN, T("13:00"), T("15:00"))])
    _, rec = E.classify_new_punch(e, window_org, later)
    assert E.left_at(e, window_org, rec, later) == datetime(2026, 10, 25, 13, 0)


def test_hourly_leave_until_the_end_needs_no_exit_punch(window_org):
    hourly = E.LeaveType(3, "زمنية", unit="hour")
    leave = [E.Leave(1, hourly, SUN, SUN, T("13:00"), T("15:00"))]  # left at 13:00 for two hours
    r = one_day(emp(["2026-10-25 08:00"], leaves=leave), window_org, SUN)
    assert r.status == E.PRESENT and "left_on_leave" in r.flags and "missing_out" not in r.flags
    assert r.worked == 300 and r.label == "غادر بإجازة ساعية"
    during = one_day(emp(["2026-10-25 08:00"], leaves=leave), window_org, SUN, now=datetime(2026, 10, 25, 13, 30))
    assert during.status == E.PRESENT and "left_on_leave" in during.flags and "at_work" not in during.flags
    # a leave that ends before the exit time still needs the exit punch
    short = [E.Leave(1, hourly, SUN, SUN, T("12:00"), T("13:00"))]
    r = one_day(emp(["2026-10-25 08:00"], leaves=short), window_org, SUN)
    assert r.status == E.ABSENT and "missing_out" in r.flags
    # and not coming at all is still an absence
    assert one_day(emp(leaves=leave), window_org, SUN).status == E.ABSENT


def test_weekly_duty_ignores_a_punch_after_its_end(duty_org):
    days = week(guard(["2026-10-25 07:55", "2026-11-01 08:10", "2026-11-01 09:30"]), duty_org)
    assert days[6].check_out == datetime(2026, 11, 1, 8, 10) and "extra_punch" in days[6].flags
    assert days[6].overtime == 0 and [d.status for d in days[:7]] == [E.PRESENT] * 7


def test_personal_times(window_org):
    e = emp(["2026-10-25 09:20", "2026-10-25 13:50"])
    e.own_start, e.own_end = T("09:00"), T("14:00")
    r = one_day(e, window_org, SUN)
    assert r.shift.start == datetime(2026, 10, 25, 9, 0) and r.shift.end == datetime(2026, 10, 25, 14, 0)
    assert (r.status, r.late, r.early) == (E.PRESENT, 0, 10)  # on time until 09:30; exits from 13:45
    e.punches = [E.Punch(1, datetime(2026, 10, 25, 10, 5))]
    assert one_day(e, window_org, SUN).status == E.ABSENT  # absent after 10:00 for this person
    only_exit = emp(["2026-10-25 08:00", "2026-10-25 13:00"])
    only_exit.own_end = T("13:00")
    assert one_day(only_exit, window_org, SUN).status == E.PRESENT
    # the group's schedule itself is unchanged
    assert one_day(emp(["2026-10-25 08:00", "2026-10-25 13:00"]), window_org, SUN).status == E.ABSENT
