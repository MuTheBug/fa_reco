"""Exporting the employees and the punches, to Excel or CSV.

The employee file uses the column names of the import, so it can be changed in Excel (for
example «السكن» for many people at once) and imported again.
"""

from __future__ import annotations

import csv
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ..core.timeutil import WEEKDAYS_AR

SOURCES = {"face": "الوجه", "manual": "إدخال يدوي", "pin": "الرمز السري", "import": "استيراد"}
EMPLOYEE_HEADERS = ["الرقم الوظيفي", "الاسم", "رقم الهاتف", "الصفة الوظيفية", "المجموعة", "السكن",
                    "أول يوم نوبة", "يبدأ (أوقات خاصة)", "يخرج (أوقات خاصة)", "عينات الوجه", "الحالة",
                    "تاريخ المباشرة", "تاريخ ترك الخدمة"]
PUNCH_HEADERS = ["الرقم الوظيفي", "الاسم", "المجموعة", "التاريخ", "اليوم", "الوقت", "البصمة", "المصدر",
                 "ملاحظة"]


def employee_rows(db) -> list[list]:
    rows = []
    for e in db.employees.list(active=None):
        rows.append([e["code"], e["full_name"], e["phone"], e["job_title"], e["group_name"] or "",
                     "بعيد" if e["far"] else "قريب", e["cycle_start"] or "", e["own_start"] or "",
                     e["own_end"] or "", e["samples"], "فعّال" if e["active"] else "غير فعّال",
                     e["hire_date"] or "", e["end_date"] or ""])
    return rows


def punch_rows(db, start: date, end: date) -> list[list]:
    """Every punch in [start, end], with what it counted as on its day (attendance or exit)."""
    reasons = {r["punch_id"]: r["reason"] for r in db.q(
        "SELECT punch_id, reason FROM corrections WHERE action = 'add'")}
    groups = db.groups.names()
    rows = []
    for emp, days in db.compute(None, start, end):
        for rec in days:
            for p in rec.punches:
                if not start <= p.ts.date() <= end:
                    continue
                role = ("حضور" if p.ts == rec.check_in else "انصراف" if p.ts == rec.check_out
                        else "لم تُحتسب")
                rows.append([emp.code, emp.name, groups.get(rec.group_id, ""), p.ts.date().isoformat(),
                             WEEKDAYS_AR[p.ts.weekday()], p.ts.strftime("%H:%M:%S"), role,
                             SOURCES.get(p.source, p.source), reasons.get(p.id, "")])
    rows.sort(key=lambda r: (r[3], r[5], r[1]))
    return rows


def write(path: str, title: str, headers: list[str], rows: list[list]) -> None:
    """An .xlsx (right to left, bold header) or, for a .csv path, UTF-8 text Excel opens in Arabic."""
    if path.lower().endswith(".csv"):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(headers)
            w.writerows(rows)
        return
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.sheet_view.rightToLeft = True
    ws.append(headers)
    for row in rows:
        ws.append(row)
    head_fill = PatternFill("solid", fgColor="0F2C4C")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center")
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:500]]) + 4
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(max(width, 10), 45)
    ws.freeze_panes = "A2"
    for r in range(2, len(rows) + 2):  # keep the leading zero of codes and phone numbers
        for c in (1, 3) if headers is EMPLOYEE_HEADERS else (1,):
            ws.cell(row=r, column=c).number_format = "@"
    wb.save(path)


def export_employees(db, path: str) -> int:
    rows = employee_rows(db)
    write(path, "الموظفون", EMPLOYEE_HEADERS, rows)
    return len(rows)


def export_punches(db, start: date, end: date, path: str) -> int:
    rows = punch_rows(db, start, end)
    write(path, "البصمات", PUNCH_HEADERS, rows)
    return len(rows)
