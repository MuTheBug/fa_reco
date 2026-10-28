"""User-defined reports ("اصنع تقريرك بنفسك").

A definition is plain JSON so it can be saved and re-run later::

    {
      "title": "...", "source": "daily",
      "columns": [{"key": "date", "title": "التاريخ"}, ...],
      "period": {"type": "this_month"} | {"type": "custom", "start": "...", "end": "..."},
      "group_ids": [], "employee_ids": [], "department": "", "include_inactive": false,
      "statuses": ["absent", "late"],
      "conditions": [{"field": "late", "op": ">", "value": 30}],
      "group_by": "name" | null,
      "sort": [{"field": "date", "desc": false}],
      "totals": true, "landscape": false, "font_scale": 1.0
    }
"""

from __future__ import annotations

from datetime import date, datetime, time

from ..db.store import Database
from . import sources as S
from .builtin import _group_sections, period_text
from .model import Column, Report, compute_totals

OPERATORS = {
    "=": "يساوي",
    "!=": "لا يساوي",
    ">": "أكبر من",
    ">=": "أكبر من أو يساوي",
    "<": "أصغر من",
    "<=": "أصغر من أو يساوي",
    "contains": "يحتوي",
    "empty": "فارغ",
    "not_empty": "غير فارغ",
}


def fields_for(db: Database, source: str) -> list[tuple]:
    fields = list(S.SOURCES[source][1])
    if source == "summary":
        fields += [(f"leave:{t['id']}", t["name"], "int", "sum") for t in db.leaves.types()
                   if t["unit"] == "day"]
    return fields


def _coerce(value, kind: str):
    """Turn a condition's typed-in value into something comparable with the field."""
    if value is None or value == "":
        return None
    if kind in ("int", "minutes", "float", "hours", "percent"):
        text = str(value).strip()
        if kind == "minutes" and ":" in text:
            h, m = text.split(":", 1)
            return int(h) * 60 + int(m)
        num = float(text)
        return num / 100 if kind == "percent" and num > 1 else num
    if kind in ("time",):
        return str(value)[:5]
    if kind == "date":
        return str(value)[:10]
    return str(value)


def _comparable(v, kind: str):
    if v is None:
        return None
    if kind == "time" and isinstance(v, (datetime, time)):
        return v.strftime("%H:%M")
    if kind == "date" and isinstance(v, date):
        return v.isoformat()
    if kind == "datetime" and isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return v


def _match(row: dict, cond: dict, kinds: dict) -> bool:
    field, op = cond.get("field"), cond.get("op", "=")
    kind = kinds.get(field, "text")
    v = _comparable(row.get(field), kind)
    if op == "empty":
        return v in (None, "", 0)
    if op == "not_empty":
        return v not in (None, "", 0)
    target = _coerce(cond.get("value"), kind)
    if op == "contains":
        return target is not None and str(target) in str(v or "")
    if v is None or target is None:
        return op == "!=" and v != target
    if isinstance(target, float) and isinstance(v, (int, float)):
        v = float(v)
    elif not isinstance(v, type(target)):
        v, target = str(v), str(target)
    try:
        return {"=": v == target, "!=": v != target, ">": v > target, ">=": v >= target,
                "<": v < target, "<=": v <= target}[op]
    except (KeyError, TypeError):
        return False


def run(db: Database, definition: dict, today: date | None = None) -> Report:
    today = today or db.now().date()
    source = definition.get("source", "daily")
    if source not in S.SOURCES:
        raise ValueError(f"unknown source {source}")
    start, end = S.resolve_period(definition.get("period"), today)
    groups = definition.get("group_ids") or None
    ids = S.select_employees(db, start, end, groups, definition.get("employee_ids") or None,
                             definition.get("department", ""), definition.get("include_inactive", False))
    if source == "daily":
        rows = S.daily_rows(db, start, end, ids, groups, definition.get("statuses") or None,
                            now=db.now())
    elif source == "summary":
        rows, _ = S.summary_rows(db, start, end, ids, groups, now=db.now())
    elif source == "punches":
        rows = S.punch_rows(db, start, end, ids, definition.get("include_deleted", False))
    elif source == "leaves":
        rows = S.leave_rows(db, start, end, ids, None, definition.get("include_cancelled", False))
    elif source == "corrections":
        rows = S.correction_rows(db, start, end, ids)
    else:
        rows = S.employee_rows(db, ids)

    fields = {f[0]: f for f in fields_for(db, source)}
    kinds = {k: f[2] for k, f in fields.items()}
    for cond in definition.get("conditions") or []:
        rows = [r for r in rows if _match(r, cond, kinds)]

    for spec in reversed(definition.get("sort") or []):
        key, kind = spec.get("field"), kinds.get(spec.get("field"), "text")
        rows.sort(key=lambda r: (r.get(key) is None, _comparable(r.get(key), kind) or ""),
                  reverse=bool(spec.get("desc")))

    columns = []
    for c in definition.get("columns") or []:
        f = fields.get(c.get("key"))
        if f is None:
            continue
        columns.append(Column(f[0], c.get("title") or f[1], f[2], float(c.get("width", 1.0)),
                              f[3] if definition.get("totals", True) else ""))
    if not columns:
        columns = [Column(k, f[1], f[2], total=f[3]) for k, f in list(fields.items())[:8]]

    title = definition.get("title") or S.SOURCES[source][0]
    rep = Report(title, columns, rows, subtitle=period_text(start, end) if source != "employees" else "",
                 landscape=bool(definition.get("landscape")),
                 font_scale=float(definition.get("font_scale", 1.0)))
    if definition.get("totals", True):
        rep.totals = compute_totals(columns, rows)
    group_by = definition.get("group_by")
    if group_by and group_by in fields:
        rep.sections = _group_sections(rows, group_by, columns)
        for sec in rep.sections:
            sec.title = f"{fields[group_by][1]}: {sec.title}"
    rep.meta["org"] = db.settings.get("org.name")
    rep.meta["org_subtitle"] = db.settings.get("org.subtitle")
    rep.meta["generated"] = db.now()
    rep.meta["user"] = (db.user.full_name or db.user.username) if db.user else ""
    rep.signatures = db.settings.get("reports.signatures") or []
    return rep
