"""Date/time helpers and Arabic formatting."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

# date.weekday(): Monday == 0 ... Sunday == 6
WEEKDAYS_AR = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
# One-letter forms used in narrow table headers (أحد=ح، اثنين=ن، ثلاثاء=ث ...).
WEEKDAYS_SHORT = ("ن", "ث", "ر", "خ", "ج", "س", "ح")
# Display order for week editors: the Arab working week starts on Sunday.
WEEK_ORDER = (6, 0, 1, 2, 3, 4, 5)

MONTHS_LEVANT = ("كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب",
                 "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول")
MONTHS_EGYPT = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس",
                "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")

_AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


class Fmt:
    """Display preferences shared by the UI and the reports."""

    arabic_digits: bool = False
    month_style: str = "levant"  # "levant" (كانون الثاني) | "egypt" (يناير) | "both"

    @classmethod
    def configure(cls, arabic_digits: bool | None = None, month_style: str | None = None):
        if arabic_digits is not None:
            cls.arabic_digits = bool(arabic_digits)
        if month_style:
            cls.month_style = month_style


def digits(text) -> str:
    s = "" if text is None else str(text)
    return s.translate(_AR_DIGITS) if Fmt.arabic_digits else s


def month_name(m: int) -> str:
    if Fmt.month_style == "egypt":
        return MONTHS_EGYPT[m - 1]
    if Fmt.month_style == "both":
        return f"{MONTHS_LEVANT[m - 1]} ({MONTHS_EGYPT[m - 1]})"
    return MONTHS_LEVANT[m - 1]


def weekday_name(d: date) -> str:
    return WEEKDAYS_AR[d.weekday()]


def fmt_date(d: date | None) -> str:
    if d is None:
        return ""
    return digits(d.strftime("%Y/%m/%d"))


def fmt_date_long(d: date) -> str:
    return f"{weekday_name(d)} {digits(d.day)} {month_name(d.month)} {digits(d.year)}"


def fmt_time(t: datetime | time | None, seconds: bool = False) -> str:
    if t is None:
        return ""
    return digits(t.strftime("%H:%M:%S" if seconds else "%H:%M"))


def fmt_minutes(m: int | float | None, blank_zero: bool = True) -> str:
    """Minutes -> H:MM (e.g. 125 -> 2:05)."""
    if m is None or (blank_zero and not m):
        return ""
    m = int(round(m))
    sign = "-" if m < 0 else ""
    m = abs(m)
    return digits(f"{sign}{m // 60}:{m % 60:02d}")


def fmt_month(year: int, month: int) -> str:
    return f"{month_name(month)} {digits(year)}"


def parse_date(s: str | date | None) -> date | None:
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return date.fromisoformat(s[:10])


def parse_time(s: str | time | None) -> time | None:
    if s is None or s == "":
        return None
    if isinstance(s, time):
        return s
    parts = [int(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


def parse_dt(s: str | datetime) -> datetime:
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s)


def iso_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def iso_time(t: time | None) -> str | None:
    return None if t is None else t.strftime("%H:%M")


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return first, nxt - timedelta(days=1)


def minutes_between(a: datetime, b: datetime) -> int:
    """Whole minutes from a to b (floor), never negative."""
    return max(0, int((b - a).total_seconds() // 60))
