import os
from datetime import date

import pytest
from openpyxl import load_workbook

from hadir.core.timeutil import month_bounds
from hadir.demo import demo_database
from hadir.reports import builtin, custom
from hadir.reports.excel import export_xlsx

TODAY = date(2026, 10, 28)
SHOTS = os.environ.get("HADIR_SHOTS")  # optional folder to save page images for review


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return demo_database(tmp_path_factory.mktemp("rep") / "demo.db", TODAY)


@pytest.mark.parametrize("report_id", [c[0] for c in builtin.CATALOG])
def test_builtin_reports_render(report_id, db, qapp, tmp_path):
    from hadir.reports.render import export_pdf, render_images

    params = builtin.default_params(report_id, TODAY)
    if report_id not in ("daily", "monthly_grid", "leave_balance"):
        params["start"] = date(2026, 9, 23)
    if report_id == "daily":
        params["day"] = date(2026, 10, 27)
    rep = builtin.build(db, report_id, params)
    assert rep.title and rep.columns
    rows = list(rep.all_rows())
    if report_id not in ("security",):
        assert rows, f"{report_id} produced no rows"
    xlsx = tmp_path / f"{report_id}.xlsx"
    export_xlsx(rep, str(xlsx))
    ws = load_workbook(xlsx).active
    assert ws.sheet_view.rightToLeft
    assert int(ws.page_setup.paperSize) == 9  # A4
    pdf = tmp_path / f"{report_id}.pdf"
    pages = export_pdf(rep, str(pdf))
    assert pages >= 1 and pdf.read_bytes()[:4] == b"%PDF"
    images = render_images(rep, dpi=90)
    # wrapping differs slightly between 300 dpi (PDF) and 90 dpi (preview) font sizes
    assert abs(len(images) - pages) <= max(2, pages // 5)
    if SHOTS:
        for i, img in enumerate(images[:2]):
            img.save(os.path.join(SHOTS, f"report_{report_id}_{i + 1}.png"))


def test_daily_report_content(db):
    rep = builtin.build(db, "daily", {"day": date(2026, 10, 27)})
    statuses = {r["status_code"] for r in rep.rows}
    assert "present" in statuses
    assert len(rep.rows) == 24
    assert rep.signatures and rep.meta["org"] == "دائرة تجريبية"


def test_monthly_summary_totals(db):
    start, end = month_bounds(2026, 10)
    rep = builtin.build(db, "monthly_summary", {"start": start, "end": TODAY})
    rows = list(rep.all_rows())
    assert sum(r["present"] for r in rows) == rep.totals["present"]
    assert all(r["workdays"] >= r["present"] - r["leave_days"] for r in rows)


def test_corrections_report_has_reasons(db):
    rep = builtin.build(db, "corrections", {"start": date(2026, 9, 1), "end": TODAY})
    assert rep.rows and all(r["reason"] for r in rep.rows)


def test_custom_report_conditions_grouping_sorting(db):
    definition = {
        "title": "المتأخرون أكثر من ربع ساعة",
        "source": "daily",
        "columns": [{"key": "date"}, {"key": "name", "title": "الموظف"}, {"key": "check_in"},
                    {"key": "late"}],
        "period": {"type": "custom", "start": "2026-09-23", "end": "2026-10-27"},
        "conditions": [{"field": "late", "op": ">", "value": "0:15"}],
        "group_by": "group",
        "sort": [{"field": "late", "desc": True}],
    }
    rep = custom.run(db, definition, TODAY)
    rows = list(rep.all_rows())
    assert rows and all(r["late"] > 15 for r in rows)
    assert rep.columns[1].title == "الموظف"
    assert rep.sections and all(s.title.startswith("المجموعة: ") for s in rep.sections)
    for sec in rep.sections:
        lates = [r["late"] for r in sec.rows]
        assert lates == sorted(lates, reverse=True)


def test_custom_report_sources(db):
    for source in custom.S.SOURCES:
        rep = custom.run(db, {"source": source, "period": {"type": "last_month"}}, TODAY)
        assert rep.columns


def test_relative_periods():
    from hadir.reports.sources import resolve_period

    assert resolve_period({"type": "this_month"}, TODAY) == (date(2026, 10, 1), date(2026, 10, 31))
    assert resolve_period({"type": "last_month"}, TODAY) == (date(2026, 9, 1), date(2026, 9, 30))
    assert resolve_period({"type": "this_week"}, TODAY) == (date(2026, 10, 25), date(2026, 10, 31))
