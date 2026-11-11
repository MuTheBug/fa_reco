"""Telegram notifications: a summary of the day's attendance as a picture (numbers, no names) at
chosen times, and optionally a message for every check-in/check-out.

This is the only feature that uses the internet, and it is off until an administrator enters
a bot token and the id of a group or channel.  Messages are first stored in the database
(``outbox``) and a background thread sends them, so the attendance screen never waits for the
network and nothing is lost while the computer is offline: reports wait up to a month, and the
screen reminds people to connect the computer to the internet while they wait.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, time, timedelta

from ..core import engine as E
from ..core.timeutil import digits, fmt_date, fmt_time, parse_dt, weekday_name
from . import summary as S

API = "https://api.telegram.org/bot{token}/{method}"
GAP_SECONDS = 3.1  # Telegram allows about 20 messages a minute in one group
GIVE_UP_DAYS = 3  # unsent punch messages older than this are dropped (they would arrive out of date)
REPORT_KEEP_DAYS = 30  # unsent reports wait this long for the internet
ALERT_AFTER = timedelta(minutes=10)  # a report waiting this long is shown on the screen
NETWORK_ERROR = "تعذّر الاتصال بتلغرام: تأكد من اتصال الجهاز بالإنترنت."

log = logging.getLogger("hadir")


class TelegramError(Exception):
    """A failure with an Arabic message; ``permanent`` ones need the settings fixed."""

    def __init__(self, message: str, permanent: bool = False, retry_after: int | None = None):
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


# ---------------------------------------------------------------- Bot API
def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "hadir-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                     + str(value).encode("utf-8") + b"\r\n")
    for name, (filename, data, ctype) in files.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"'
                     f"\r\nContent-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def call(token: str, method: str, payload: dict | None = None, timeout: float = 20, files: dict | None = None):
    token = (token or "").strip()
    if not token:
        raise TelegramError("أدخل رمز البوت أولاً.", permanent=True)
    if files:
        body, ctype = _multipart(payload or {}, files)
    else:
        body, ctype = json.dumps(payload or {}).encode("utf-8"), "application/json"
    req = urllib.request.Request(API.format(token=token, method=method), data=body,
                                 headers={"Content-Type": ctype})
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
        raise TelegramError(NETWORK_ERROR) from None
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


def send_message(token: str, chat_id: str, text: str, html: bool = False) -> None:
    payload = {"chat_id": str(chat_id).strip(), "text": text, "disable_web_page_preview": True}
    if html:
        payload["parse_mode"] = "HTML"
    call(token, "sendMessage", payload)


def send_photo(token: str, chat_id: str, photo: bytes, caption: str = "", html: bool = False) -> None:
    payload = {"chat_id": str(chat_id).strip(), "caption": caption}
    if html:
        payload["parse_mode"] = "HTML"
    call(token, "sendPhoto", payload, timeout=60, files={"photo": ("summary.png", photo, "image/png")})


def deliver(token: str, chat_id: str, text: str, photo: bytes | None = None, html: bool = False) -> None:
    """Send one queued message: a picture with its caption, or text."""
    if photo:
        send_photo(token, chat_id, photo, text, html)
    else:
        send_message(token, chat_id, text, html)


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
    block = rec.schedule.block_for(rec.date) if rec.schedule is not None and rec.shift is not None else None
    if block is not None:
        title = "🟢 التحاق بالنوبة" if punch_type == "in" else "🔴 انتهاء النوبة"
    else:
        title = "🟢 تسجيل حضور" if punch_type == "in" else "🔴 تسجيل انصراف"
    lines = [title, f"الاسم: {emp['full_name']}"]
    if emp["job_title"]:
        lines.append(f"الصفة: {emp['job_title']}")
    if punch_type == "in":
        lines.append(f"وقت الدخول: {fmt_time(ts)}")
    else:
        if rec.check_in is not None and rec.check_in != ts:
            lines.append(f"وقت الدخول: {fmt_time(rec.check_in)}")
        lines.append(f"وقت الخروج: {fmt_time(ts)}")
    if block is not None:
        lines.append(f"النوبة: من {weekday_name(block.start.date())} {fmt_date(block.start.date())}"
                     f" {fmt_time(block.start)} إلى {weekday_name(block.end.date())} {fmt_date(block.end.date())}"
                     f" {fmt_time(block.end)}")
    lines.append(f"الحالة: {day_status(rec, punch_type)}")
    lines.append(f"التاريخ: {weekday_name(ts.date())} {fmt_date(ts.date())}")
    return "\n".join(lines)


def queue_punch(db, emp, punch_type: str, rec: E.DayRecord, ts: datetime) -> None:
    if enabled(db) and db.settings.get("telegram.punches"):
        db.outbox.add("punch", punch_text(emp, punch_type, rec, ts))


def report_picture(summary: S.DaySummary) -> bytes | None:
    """The summary drawn as a picture (None if it cannot be drawn: the text alone goes out)."""
    try:
        from .card import render

        return render(summary)
    except Exception:
        log.exception("telegram: drawing the summary")
        return None


def queue_report(db, day: date, at: time | None = None) -> int:
    """Queue the day's summary; returns 1, or 0 when nobody is due at work (or it is switched off).
    It replaces a summary of the same day that is still waiting to be sent."""
    summary = S.day_summary(db, day, at) if enabled(db) else None
    if summary is None:
        return 0
    photo = report_picture(summary)
    text = S.caption(summary) if photo else S.report_text(summary)
    db.outbox.add("report", text, photo=photo, day=day.isoformat())
    return 1


def waiting_alert(db, now: datetime) -> str:
    """What to show on the screen while reports cannot be sent ("" when nothing is held up)."""
    if not enabled(db):
        return ""
    w = db.outbox.waiting()
    if not w["count"] or not w["attempts"] or now - parse_dt(w["oldest"]) < ALERT_AFTER:
        return ""
    oldest = parse_dt(w["oldest"])
    since = (f"الساعة {fmt_time(oldest)}" if oldest.date() == now.date()
             else f"{weekday_name(oldest.date())} {fmt_date(oldest.date())}")
    if not w["error"] or w["error"] == NETWORK_ERROR:
        return (f"لا يوجد اتصال بالإنترنت: تقارير الدوام بانتظار الإرسال إلى تلغرام ({digits(w['count'])})"
                f" منذ {since}. صِل الجهاز بالإنترنت وستُرسل تلقائياً.")
    return (f"تعذّر إرسال تقارير الدوام إلى تلغرام ({digits(w['count'])}) منذ {since}: {w['error']}"
            " راجع إعدادات تلغرام.")


def parse_times(text: str) -> list[time]:
    """«09:05، 17:05» -> [09:05, 17:05]; ValueError for anything that is not a time of day."""
    out = set()
    for part in re.split(r"[,،\s]+", (text or "").strip()):
        if not part:
            continue
        hh, _, mm = part.partition(":")
        if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            raise ValueError(part)
        out.add(time(int(hh), int(mm)))
    return sorted(out)


def report_due(db, now: datetime) -> time | None:
    """The latest report time that has passed today and whose report was not sent yet."""
    if not (enabled(db) and db.settings.get("telegram.daily_list")):
        return None
    try:
        times = parse_times(db.settings.get("telegram.daily_time")) or [time(17, 5)]
    except ValueError:
        times = [time(17, 5)]
    passed = [t for t in times if t <= now.time()]
    if not passed:
        return None
    slot = f"{now.date().isoformat()} {passed[-1].strftime('%H:%M')}"
    last = db.settings.get("telegram.last_list") or ""
    if last >= slot or last == now.date().isoformat():  # a bare date: version 1.1 sent today's list
        return None
    return passed[-1]


# ---------------------------------------------------------------- sender
class Sender(threading.Thread):
    """Sends queued messages in order, one every few seconds, and waits after a failure."""

    def __init__(self, db, interval: float = 5.0, transport=deliver, gap: float = GAP_SECONDS):
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
        if db.outbox.drop_older_than(now - timedelta(days=GIVE_UP_DAYS), "punch"):
            db.events.add("telegram", f"أُهملت رسائل لم يمكن إرسالها خلال {digits(GIVE_UP_DAYS)} أيام")
        if db.outbox.drop_older_than(now - timedelta(days=REPORT_KEEP_DAYS), "report"):
            db.events.add("telegram", f"أُهملت تقارير لم يمكن إرسالها خلال {digits(REPORT_KEEP_DAYS)} يوماً")
        token, chat = db.settings.get("telegram.token"), db.settings.get("telegram.chat_id")
        sent = 0
        for m in db.outbox.pending():
            if self._stop_event.is_set():
                break
            try:
                self.transport(token, chat, m["text"], m["photo"], m["kind"] == "report")
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
