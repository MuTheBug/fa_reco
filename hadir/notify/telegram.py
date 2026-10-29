"""Telegram notifications: a message for every check-in/check-out and a daily list by name.

This is the only feature that uses the internet, and it is off until an administrator enters
a bot token and the id of a group or channel.  Messages are first stored in the database
(``outbox``) and a background thread sends them, so the attendance screen never waits for the
network and nothing is lost while the computer is offline.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

from ..core import engine as E
from ..core.timeutil import digits, fmt_date, fmt_time, parse_time, weekday_name

API = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT = 3900  # Telegram accepts up to 4096 characters per message
GAP_SECONDS = 3.1  # Telegram allows about 20 messages a minute in one group
GIVE_UP_DAYS = 3  # unsent messages older than this are dropped (they would arrive out of date)

log = logging.getLogger("hadir")


class TelegramError(Exception):
    """A failure with an Arabic message; ``permanent`` ones need the settings fixed."""

    def __init__(self, message: str, permanent: bool = False, retry_after: int | None = None):
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


# ---------------------------------------------------------------- Bot API
def call(token: str, method: str, payload: dict | None = None, timeout: float = 20):
    token = (token or "").strip()
    if not token:
        raise TelegramError("أدخل رمز البوت أولاً.", permanent=True)
    req = urllib.request.Request(API.format(token=token, method=method),
                                 data=json.dumps(payload or {}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            body = {}
        raise _api_error(exc.code, body) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise TelegramError("تعذّر الاتصال بتلغرام: تأكد من اتصال الجهاز بالإنترنت.") from None
    if not body.get("ok"):
        raise _api_error(int(body.get("error_code") or 0), body)
    return body.get("result")


def _api_error(code: int, body: dict) -> TelegramError:
    desc = str(body.get("description") or "")
    params = body.get("parameters") or {}
    if code in (401, 404):
        return TelegramError("رمز البوت غير صحيح.", permanent=True)
    if code == 429:
        return TelegramError("طلب تلغرام التمهّل قليلاً قبل إرسال رسائل أخرى.",
                             retry_after=int(params.get("retry_after") or 30))
    if params.get("migrate_to_chat_id"):
        return TelegramError(f"تغيّر معرّف المجموعة إلى {params['migrate_to_chat_id']}؛ اكتبه في الإعدادات.",
                             permanent=True)
    if code == 400 and "chat not found" in desc.lower():
        return TelegramError("لم يُعثر على المجموعة: تأكد من معرّفها ومن إضافة البوت إليها.", permanent=True)
    if code == 403:
        return TelegramError("البوت ليس عضواً في المجموعة أو لا يملك صلاحية الإرسال فيها.", permanent=True)
    return TelegramError(f"رفض تلغرام الرسالة ({code}): {desc}", permanent=400 <= code < 500)


def send_message(token: str, chat_id: str, text: str) -> None:
    call(token, "sendMessage", {"chat_id": str(chat_id).strip(), "text": text,
                                "disable_web_page_preview": True})


def find_chats(token: str) -> list[tuple[str, str]]:
    """Groups and channels that recently wrote to the bot or added it: [(id, title)]."""
    found: dict[str, str] = {}
    for update in call(token, "getUpdates", {"limit": 100}) or []:
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("type") in ("group", "supergroup", "channel"):
                found[str(chat["id"])] = chat.get("title") or str(chat["id"])
    return list(found.items())


# ---------------------------------------------------------------- messages
def enabled(db) -> bool:
    s = db.settings
    return bool(s.get("telegram.enabled") and s.get("telegram.token") and s.get("telegram.chat_id"))


def day_status(rec: E.DayRecord, punch_type: str | None = None) -> str:
    """Short Arabic status of the day (as shown after a punch or in the daily list)."""
    if rec.status != E.PRESENT:
        return rec.label
    if punch_type == "in" or (punch_type is None and rec.check_out is None):
        return f"متأخر {digits(rec.late)} دقيقة" if rec.late else "في الموعد"
    if rec.early:
        return f"خروج مبكر {digits(rec.early)} دقيقة"
    if rec.late:
        return f"متأخر {digits(rec.late)} دقيقة"
    return "حاضر"


def punch_text(emp, punch_type: str, rec: E.DayRecord, ts: datetime) -> str:
    """Name, job description, entry (and exit) time and the day's status."""
    lines = ["🟢 تسجيل حضور" if punch_type == "in" else "🔴 تسجيل انصراف",
             f"الاسم: {emp['full_name']}"]
    if emp["job_title"]:
        lines.append(f"الصفة: {emp['job_title']}")
    if punch_type == "in":
        lines.append(f"وقت الدخول: {fmt_time(ts)}")
    else:
        if rec.check_in is not None and rec.check_in != ts:
            lines.append(f"وقت الدخول: {fmt_time(rec.check_in)}")
        lines.append(f"وقت الخروج: {fmt_time(ts)}")
    lines.append(f"الحالة: {day_status(rec, punch_type)}")
    lines.append(f"التاريخ: {weekday_name(ts.date())} {fmt_date(ts.date())}")
    return "\n".join(lines)


def daily_list(db, day: date) -> list[str]:
    """The day's attendance by name, split into messages; [] when nobody works that day."""
    from ..reports.sources import select_employees

    ids = select_employees(db, day, day)
    jobs = {r["id"]: r["job_title"] for r in db.q("SELECT id, job_title FROM employees")}
    people = sorted(db.compute(ids, day, day), key=lambda er: er[0].name)
    if not any(rec.shift is not None and rec.status not in (E.HOLIDAY, E.REST) for _, [rec] in people):
        return []
    counts = {"present": 0, "late": 0, "absent": 0, "leave": 0}
    lines = []
    for emp, [rec] in people:
        if rec.status in (E.INACTIVE, E.FUTURE):
            continue
        parts = [f"{digits(len(lines) + 1)}. {emp.name}"]
        if jobs.get(emp.id):
            parts.append(jobs[emp.id])
        if rec.check_in:
            parts.append(f"دخول {fmt_time(rec.check_in)}")
        if rec.check_out:
            parts.append(f"خروج {fmt_time(rec.check_out)}")
        parts.append(day_status(rec))
        lines.append(" — ".join(parts))
        if rec.counts_present:
            counts["present"] += 1
        if rec.late:
            counts["late"] += 1
        if rec.status == E.ABSENT:
            counts["absent"] += 1
        elif rec.status == E.LEAVE:
            counts["leave"] += 1
    head = [f"📋 قائمة الحضور — {weekday_name(day)} {fmt_date(day)}", db.settings.get("org.name") or "", ""]
    foot = ["", f"الحاضرون: {digits(counts['present'])} • المتأخرون: {digits(counts['late'])}"
                f" • الغائبون: {digits(counts['absent'])} • المجازون: {digits(counts['leave'])}"]
    messages, current = [], head
    for line in lines + foot:
        if len("\n".join(current + [line])) > MAX_TEXT:
            messages.append("\n".join(current))
            current = []
        current.append(line)
    messages.append("\n".join(current))
    return messages


def queue_punch(db, emp, punch_type: str, rec: E.DayRecord, ts: datetime) -> None:
    if enabled(db) and db.settings.get("telegram.punches"):
        db.outbox.add("punch", punch_text(emp, punch_type, rec, ts))


def queue_daily_list(db, day: date) -> int:
    """Queue the day's list; returns the number of messages (0 when nobody works that day)."""
    messages = daily_list(db, day) if enabled(db) else []
    for text in messages:
        db.outbox.add("daily_list", text)
    return len(messages)


def daily_list_due(db, now: datetime) -> bool:
    """Once a day, after the chosen time."""
    if not (enabled(db) and db.settings.get("telegram.daily_list")):
        return False
    try:
        at = parse_time(db.settings.get("telegram.daily_time")) or parse_time("17:05")
    except ValueError:
        at = parse_time("17:05")
    return now.time() >= at and db.settings.get("telegram.last_list") != now.date().isoformat()


# ---------------------------------------------------------------- sender
class Sender(threading.Thread):
    """Sends queued messages in order, one every few seconds, and waits after a failure."""

    def __init__(self, db, interval: float = 5.0, transport=send_message, gap: float = GAP_SECONDS):
        super().__init__(name="telegram", daemon=True)
        self.db = db
        self.interval = interval
        self.transport = transport
        self.gap = gap
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._pause_until: datetime | None = None
        self._failures = 0
        self._reported = ""

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.step()
            except Exception:
                log.exception("telegram sender")
            self._wake.wait(self.interval)
            self._wake.clear()

    def wake(self) -> None:
        """Try again now (after the settings were changed or a message was queued)."""
        self._pause_until = None
        self._wake.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def step(self) -> int:
        """Send what is waiting; returns how many messages went out."""
        db = self.db
        if not enabled(db):
            return 0
        now = db.now()
        if self._pause_until and now < self._pause_until:
            return 0
        if db.outbox.drop_older_than(now - timedelta(days=GIVE_UP_DAYS)):
            db.events.add("telegram", f"أُهملت رسائل لم يمكن إرسالها خلال {digits(GIVE_UP_DAYS)} أيام")
        token, chat = db.settings.get("telegram.token"), db.settings.get("telegram.chat_id")
        sent = 0
        for m in db.outbox.pending():
            if self._stop_event.is_set():
                break
            try:
                self.transport(token, chat, m["text"])
            except TelegramError as exc:
                db.outbox.mark_failed(m["id"], str(exc))
                self._failures += 1
                wait = exc.retry_after or min(900, 30 * 2 ** min(self._failures - 1, 5))
                if exc.permanent:
                    wait = max(wait, 900)
                self._pause_until = db.now() + timedelta(seconds=wait)
                if str(exc) != self._reported:
                    self._reported = str(exc)
                    db.events.add("telegram", f"تعذّر إرسال إشعار تلغرام: {exc}")
                break
            db.outbox.mark_sent(m["id"])
            self._failures, self._reported = 0, ""
            sent += 1
            self._stop_event.wait(self.gap)
        return sent
