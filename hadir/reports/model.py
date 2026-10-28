"""Report data model shared by the ready-made reports and the report builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from ..core.timeutil import digits, fmt_date, fmt_minutes, fmt_time

# kind -> default alignment in an RTL table ("right" is the reading start)
NUMERIC_KINDS = {"int", "minutes", "float", "percent", "hours"}


@dataclass
class Column:
    key: str
    title: str
    kind: str = "text"  # text | int | float | minutes | hours | time | date | datetime | percent
    width: float = 1.0
    total: str = ""  # "" | "sum" | "avg" | "count"

    @property
    def numeric(self) -> bool:
        return self.kind in NUMERIC_KINDS


@dataclass
class Section:
    title: str
    rows: list
    subtotal: dict | None = None


@dataclass
class Report:
    title: str
    columns: list
    rows: list = field(default_factory=list)  # list of dicts (raw values)
    subtitle: str = ""
    landscape: bool = False
    sections: list | None = None  # optional grouping (list[Section])
    totals: dict | None = None
    notes: list = field(default_factory=list)
    signatures: list = field(default_factory=list)
    font_scale: float = 1.0
    page_per_section: bool = False
    # optional per-row tone: key in row dict "_tone" in {"bad", "warn", "ok", "muted", "info"}
    meta: dict = field(default_factory=dict)

    def all_rows(self):
        if self.sections:
            for s in self.sections:
                yield from s.rows
        else:
            yield from self.rows


def format_value(value, kind: str) -> str:
    if value is None or value == "":
        return ""
    if kind == "minutes":
        return fmt_minutes(value)
    if kind == "hours":
        return digits(f"{value:.1f}".rstrip("0").rstrip(".")) if value else ""
    if kind == "time":
        return fmt_time(value) if isinstance(value, (datetime, time)) else digits(value)
    if kind == "date":
        return fmt_date(value) if isinstance(value, date) else digits(value)
    if kind == "datetime":
        if isinstance(value, datetime):
            return digits(value.strftime("%Y/%m/%d %H:%M"))
        return digits(value)
    if kind == "percent":
        return digits(f"{value * 100:.0f}%")
    if kind == "float":
        return digits(f"{value:.2f}")
    if kind == "int":
        return digits(value) if value else ""
    return digits(value) if isinstance(value, (int, float)) else str(value)


def compute_totals(columns, rows) -> dict:
    out = {}
    rows = list(rows)
    for c in columns:
        if not c.total:
            continue
        vals = [r.get(c.key) for r in rows if isinstance(r.get(c.key), (int, float))
                and not isinstance(r.get(c.key), bool)]
        if c.total == "sum":
            out[c.key] = sum(vals)
        elif c.total == "avg":
            out[c.key] = (sum(vals) / len(vals)) if vals else None
        elif c.total == "count":
            out[c.key] = sum(1 for r in rows if r.get(c.key) not in (None, "", 0))
    return out
