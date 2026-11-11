"""Telegram notifications and the kiosk's strict-rule behaviour (no network is used)."""

import io
import json
import urllib.error
from datetime import date, datetime, time

import pytest

from hadir.db.seed import seed_defaults
from hadir.db.store import Database
from hadir.kiosk_logic import KioskLogic
from hadir.notify import telegram as T


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db", clock=Clock(datetime(2026, 10, 25, 8, 5)))  # a Sunday
    seed_defaults(d)
    d.settings.set("org.name", "دائرة الاختبار")
    return d


def add(db, name, job="محاسب"):
    return db.employees.save(dict(full_name=name, job_title=job, group_id=db.employees.default_group(),
                                  hire_date="2026-01-01"))


def switch_on(db):
    db.settings.set_many({"telegram.enabled": True, "telegram.token": "123:ABC", "telegram.chat_id": "-100"})


class FakeTransport:
    def __init__(self, fail=None):
        self.sent = []
        self.extras = []
        self.fail = list(fail or [])

    def __call__(self, token, chat, text, photo=None, html=False):
        if self.fail:
            raise self.fail.pop(0)
        self.sent.append((token, chat, text))
        self.extras.append((photo, html))


# ---------------------------------------------------------------- messages
def test_punch_messages_carry_name_job_and_times(db):
    switch_on(db)
    assert not db.settings.get("telegram.punches")  # off unless chosen: too many with many employees
    db.settings.set("telegram.punches", True)
    eid = add(db, "أحمد علي")
    logic = KioskLogic(db)
    res = logic.register(eid, datetime(2026, 10, 25, 8, 5))
    assert res.kind == "punched" and res.tone == "ok"
    db.clock.now = datetime(2026, 10, 25, 14, 50)
    logic.register(eid, datetime(2026, 10, 25, 14, 50))
    first, second = [r["text"] for r in db.q("SELECT text FROM outbox ORDER BY id")]
    assert "تسجيل حضور" in first and "الاسم: أحمد علي" in first and "الصفة: محاسب" in first
    assert "وقت الدخول: 08:05" in first and "الحالة: في الموعد" in first
    assert "تسجيل انصراف" in second and "وقت الدخول: 08:05" in second and "وقت الخروج: 14:50" in second


def test_nothing_is_queued_while_switched_off(db):
    eid = add(db, "سارة")
    KioskLogic(db).register(eid, datetime(2026, 10, 25, 8, 5))
    assert db.scalar("SELECT COUNT(*) FROM outbox") == 0
    switch_on(db)
    db.settings.set("telegram.punches", False)
    db.clock.now = datetime(2026, 10, 25, 14, 50)
    KioskLogic(db).register(eid, datetime(2026, 10, 25, 14, 50))
    assert db.scalar("SELECT COUNT(*) FROM outbox") == 0


def a_day_of_work(db):
    """Four administrative staff (on time, late, absent, absent after the cutoff) and two guards."""
    a, b, c = add(db, "باسم"), add(db, "أحمد", "مهندس"), add(db, "زينب")
    late = add(db, "علي", "كاتب")
    guards = db.scalar("SELECT id FROM emp_groups WHERE name = 'الحرس'")
    on_duty = db.employees.save(dict(full_name="حارس أول", job_title="حارس", group_id=guards,
                                     hire_date="2026-01-01", cycle_start="2026-10-25"))
    db.employees.save(dict(full_name="حارس ثانٍ", job_title="حارس", group_id=guards, hire_date="2026-01-01",
                           cycle_start="2026-10-26"))
    for eid, times in ((a, ("08:00", "14:50")), (b, ("08:40", "14:46")), (late, ("09:20", "14:50")),
                       (on_duty, ("08:00",))):
        for t in times:
            db.punches.add_face(eid, datetime.fromisoformat(f"2026-10-25 {t}"))
    return c


def test_the_summary_counts_without_names(db):
    from hadir.notify import summary as S

    a_day_of_work(db)
    db.clock.now = datetime(2026, 10, 25, 17, 5)
    s = S.day_summary(db, date(2026, 10, 25), time(17, 5))
    t = s.total
    assert (t.due, t.on_time, t.late, t.late_minutes, t.absent, t.leave, t.pending, t.off) == (5, 2, 1, 40, 2, 0, 0, 1)
    assert t.absences == {"no_show": 1, "absent_late": 1} and round(t.rate, 2) == 0.6
    groups = dict(s.groups)
    assert list(groups) == ["الموظفون الإداريون", "الحرس"]  # the weekly guards have nobody
    assert (groups["الموظفون الإداريون"].due, groups["الموظفون الإداريون"].present) == (4, 2)
    assert (groups["الحرس"].due, groups["الحرس"].on_time, groups["الحرس"].off) == (1, 1, 1)
    assert S.caption(s).splitlines() == [
        "<b>📊 ملخص الدوام — الأحد 2026/10/25</b>",
        "دائرة الاختبار • حتى الساعة 17:05",
        "",
        "✅ حضر <b>3</b> من 5 (60%)",
        "⏰ متأخرون: <b>1</b> (معدل التأخير 40 دقيقة)",
        "❌ غائبون: <b>2</b>",
    ]
    text = S.report_text(s)
    assert "الغياب: لم يحضروا 1، حضروا بعد وقت الغياب 1" in text
    assert "👥 الحرس: حضر 1 من 1" in text
    assert not any(name in text for name in ("باسم", "أحمد", "زينب", "علي", "حارس أول"))
    assert S.day_summary(db, date(2026, 10, 30)) is None  # Friday: nobody is due


def test_the_report_is_a_picture_with_a_short_caption(db, qapp):
    a_day_of_work(db)
    db.clock.now = datetime(2026, 10, 25, 9, 5)
    assert T.queue_report(db, date(2026, 10, 25), time(9, 5)) == 0  # switched off
    switch_on(db)
    assert T.queue_report(db, date(2026, 10, 25), time(9, 5)) == 1
    [m] = db.outbox.pending()
    assert m["kind"] == "report" and m["day"] == "2026-10-25" and m["photo"][:8] == b"\x89PNG\r\n\x1a\n"
    assert m["text"].startswith("<b>📊 ملخص الدوام — الأحد 2026/10/25</b>") and "باسم" not in m["text"]
    assert T.queue_report(db, date(2026, 10, 30)) == 0


def test_without_a_picture_the_summary_goes_as_text(db, monkeypatch):
    a_day_of_work(db)
    switch_on(db)
    monkeypatch.setattr(T, "report_picture", lambda summary: None)
    db.clock.now = datetime(2026, 10, 25, 17, 5)
    assert T.queue_report(db, date(2026, 10, 25), time(17, 5)) == 1
    [m] = db.outbox.pending()
    assert m["photo"] is None and "👥 الموظفون الإداريون: حضر 2 من 4" in m["text"]


def test_a_newer_summary_replaces_the_one_still_waiting(db):
    switch_on(db)
    db.outbox.add("report", "09:05", photo=b"a", day="2026-10-25")
    db.outbox.add("report", "أمس", photo=b"b", day="2026-10-24")
    db.outbox.add("report", "17:05", photo=b"c", day="2026-10-25")
    assert [m["text"] for m in db.outbox.pending()] == ["أمس", "17:05"]
    assert db.scalar("SELECT COUNT(*) FROM outbox WHERE photo IS NOT NULL") == 2


def test_reports_are_due_once_after_each_time(db):
    switch_on(db)  # by default at 09:05 and 17:05
    assert T.report_due(db, datetime(2026, 10, 25, 9, 0)) is None
    assert T.report_due(db, datetime(2026, 10, 25, 9, 6)) == time(9, 5)
    db.settings.set("telegram.last_list", "2026-10-25 09:05", audit=False)
    assert T.report_due(db, datetime(2026, 10, 25, 12, 0)) is None
    assert T.report_due(db, datetime(2026, 10, 25, 17, 6)) == time(17, 5)
    db.settings.set("telegram.last_list", "2026-10-25 17:05", audit=False)
    assert T.report_due(db, datetime(2026, 10, 25, 23, 0)) is None
    assert T.report_due(db, datetime(2026, 10, 26, 9, 10)) == time(9, 5)
    assert T.parse_times("17:05، 9:05 09:05") == [time(9, 5), time(17, 5)]
    with pytest.raises(ValueError):
        T.parse_times("25:00")


def test_weekly_guard_joins_and_ends_the_duty(db):
    from hadir.core import engine as E

    switch_on(db)
    db.settings.set("telegram.punches", True)
    weekly = db.scalar("SELECT id FROM emp_groups WHERE name = 'حرس النوبات الأسبوعية'")
    eid = db.employees.save(dict(full_name="حارس النوبة", job_title="حارس", group_id=weekly,
                                 hire_date="2026-01-01", cycle_start="2026-10-25"))
    logic = KioskLogic(db)
    db.clock.now = datetime(2026, 10, 25, 7, 58)
    res = logic.register(eid, datetime(2026, 10, 25, 7, 58))
    assert res.title == "تم تسجيل الالتحاق بالنوبة" and "تنتهي نوبتك الأحد 2026/11/01 الساعة 08:00" in res.lines
    db.clock.now = datetime(2026, 11, 1, 8, 4)
    res = logic.register(eid, datetime(2026, 11, 1, 8, 4))
    assert res.title == "تم تسجيل انتهاء النوبة" and res.tone == "ok"
    first, second = [r["text"] for r in db.q("SELECT text FROM outbox ORDER BY id")]
    assert first.startswith("🟢 التحاق بالنوبة")
    assert "النوبة: من الأحد 2026/10/25 08:00 إلى الأحد 2026/11/01 08:00" in first
    assert second.startswith("🔴 انتهاء النوبة") and "وقت الخروج: 08:04" in second
    [(emp, days)] = db.compute([eid], date(2026, 10, 25), date(2026, 11, 1))
    assert [d.status for d in days] == [E.PRESENT] * 7 + [E.REST]
    db.clock.now = datetime(2026, 11, 1, 9, 0)  # passing the camera after the duty has ended
    res = logic.register(eid, db.clock.now)
    assert res.kind == "left" and res.title == "انتهاء نوبتك مسجّل" and "08:04" in res.lines[0]


# ---------------------------------------------------------------- sender
def test_sender_sends_in_order_and_waits_after_a_failure(db):
    switch_on(db)
    for i in range(3):
        db.outbox.add("punch", f"رسالة {i}")
    fake = FakeTransport(fail=[T.TelegramError("تعذّر الاتصال")])
    sender = T.Sender(db, transport=fake, gap=0)
    assert sender.step() == 0  # offline: nothing sent, the first message keeps its place
    assert db.outbox.status()["pending"] == 3 and db.outbox.status()["error"] == "تعذّر الاتصال"
    assert sender.step() == 0  # still pausing
    db.clock.now = datetime(2026, 10, 25, 8, 7)
    assert sender.step() == 3
    assert [t for _, _, t in fake.sent] == ["رسالة 0", "رسالة 1", "رسالة 2"]
    assert db.outbox.status()["pending"] == 0
    assert db.scalar("SELECT COUNT(*) FROM security_events WHERE kind = 'telegram'") == 1


def test_sender_respects_retry_after_and_drops_stale_messages(db):
    switch_on(db)
    db.outbox.add("punch", "قديمة")
    db.clock.now = datetime(2026, 10, 29, 9, 0)
    db.outbox.add("punch", "جديدة")
    fake = FakeTransport(fail=[T.TelegramError("تمهّل", retry_after=40)])
    sender = T.Sender(db, transport=fake, gap=0)
    assert sender.step() == 0
    db.clock.now = datetime(2026, 10, 29, 9, 0, 30)
    assert sender.step() == 0
    db.clock.now = datetime(2026, 10, 29, 9, 0, 41)
    assert sender.step() == 1 and fake.sent[0][2] == "جديدة"  # the 4-day-old one was dropped


def test_reports_wait_for_the_internet_for_a_month(db):
    switch_on(db)
    db.outbox.add("report", "ملخص", photo=b"png", day="2026-10-25")
    db.outbox.add("punch", "بصمة")
    fake = FakeTransport()
    db.clock.now = datetime(2026, 11, 20, 9, 0)  # 26 days offline
    assert T.Sender(db, transport=fake, gap=0).step() == 1
    assert fake.sent[0][2] == "ملخص" and fake.extras[0] == (b"png", True)  # a picture, HTML caption
    assert db.scalar("SELECT photo FROM outbox WHERE kind = 'report'") is None  # not kept once sent
    db.outbox.add("report", "قديم جداً", day="2026-11-20")
    db.clock.now = datetime(2026, 12, 21, 9, 0)
    assert T.Sender(db, transport=fake, gap=0).step() == 0


def test_the_screen_says_when_reports_wait_for_the_internet(db):
    switch_on(db)
    db.clock.now = datetime(2026, 10, 25, 9, 5)
    db.outbox.add("report", "ملخص", day="2026-10-25")
    assert T.waiting_alert(db, datetime(2026, 10, 25, 9, 30)) == ""  # not tried yet
    sender = T.Sender(db, transport=FakeTransport(fail=[T.TelegramError(T.NETWORK_ERROR)]), gap=0)
    assert sender.step() == 0
    assert T.waiting_alert(db, datetime(2026, 10, 25, 9, 10)) == ""  # give it a few minutes
    text = T.waiting_alert(db, datetime(2026, 10, 25, 9, 30))
    assert text.startswith("لا يوجد اتصال بالإنترنت") and "(1)" in text and "الساعة 09:05" in text
    assert "الأحد 2026/10/25" in T.waiting_alert(db, datetime(2026, 10, 26, 8, 0))
    db.outbox.mark_failed(db.outbox.pending()[0]["id"], "رمز البوت غير صحيح.")
    assert "راجع إعدادات تلغرام" in T.waiting_alert(db, datetime(2026, 10, 25, 9, 30))
    db.outbox.mark_sent(db.outbox.pending()[0]["id"])
    assert T.waiting_alert(db, datetime(2026, 10, 25, 9, 30)) == ""


def test_sender_does_nothing_while_switched_off(db):
    db.outbox.add("punch", "x")
    fake = FakeTransport()
    assert T.Sender(db, transport=fake, gap=0).step() == 0 and not fake.sent


# ---------------------------------------------------------------- Bot API errors
def http_error(code, body):
    return urllib.error.HTTPError("https://api.telegram.org", code, "err", {},
                                  io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize("code,body,text,permanent", [
    (401, {"ok": False, "error_code": 401, "description": "Unauthorized"}, "رمز البوت غير صحيح", True),
    (400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, "لم يُعثر على المجموعة",
     True),
    (403, {"ok": False, "error_code": 403, "description": "Forbidden: bot is not a member"}, "ليس عضواً", True),
    (429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
           "parameters": {"retry_after": 17}}, "التمهّل", False),
])
def test_api_errors_are_explained(monkeypatch, code, body, text, permanent):
    def fake_urlopen(req, timeout):
        raise http_error(code, body)

    monkeypatch.setattr(T.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(T.TelegramError) as exc:
        T.send_message("123:ABC", "-100", "x")
    assert text in str(exc.value) and exc.value.permanent == permanent
    if code == 429:
        assert exc.value.retry_after == 17


def test_offline_error_and_chat_discovery(monkeypatch):
    def offline(req, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(T.urllib.request, "urlopen", offline)
    with pytest.raises(T.TelegramError) as exc:
        T.send_message("123:ABC", "-100", "x")
    assert "الإنترنت" in str(exc.value) and not exc.value.permanent

    updates = [{"message": {"chat": {"id": -100123, "type": "supergroup", "title": "الحضور"}}},
               {"message": {"chat": {"id": 55, "type": "private"}}},
               {"my_chat_member": {"chat": {"id": -100999, "type": "channel", "title": "القناة"}}}]
    monkeypatch.setattr(T, "call", lambda token, method, payload=None, timeout=20: updates)
    assert T.find_chats("123:ABC") == [("-100123", "الحضور"), ("-100999", "القناة")]


# ---------------------------------------------------------------- kiosk and strict rules
def test_kiosk_explains_absence_and_refuses_after_last_exit_time(db):
    eid = add(db, "علي")
    logic = KioskLogic(db)
    db.clock.now = datetime(2026, 10, 25, 9, 10)
    res = logic.register(eid, datetime(2026, 10, 25, 9, 10))
    assert res.kind == "punched" and res.tone == "warn" and "يُحتسب اليوم غياباً" in res.lines[0]
    db.clock.now = datetime(2026, 10, 25, 17, 20)
    res = logic.register(eid, datetime(2026, 10, 25, 17, 20))
    assert res.kind == "closed" and "17:00" in res.lines[0]
    assert db.scalar("SELECT COUNT(*) FROM punches WHERE employee_id = ?", (eid,)) == 1
    assert db.scalar("SELECT COUNT(*) FROM security_events WHERE kind = 'late_punch'") == 1


def test_kiosk_exit_window(db):
    eid = add(db, "مريم")
    logic = KioskLogic(db)
    logic.register(eid, datetime(2026, 10, 25, 8, 5))
    db.clock.now = datetime(2026, 10, 25, 13, 0)
    res = logic.register(eid, datetime(2026, 10, 25, 13, 0))  # too early to be an exit: not recorded
    assert res.kind == "early" and res.lines[:2] == ["حضورك مسجّل الساعة 08:05", "تُقبل بصمة الخروج من الساعة 14:30"]
    assert "إجازة ساعية" in res.lines[2]
    assert db.scalar("SELECT COUNT(*) FROM punches WHERE employee_id = ?", (eid,)) == 1
    db.clock.now = datetime(2026, 10, 25, 14, 35)
    res = logic.register(eid, datetime(2026, 10, 25, 14, 35))
    assert res.punch_type == "out" and res.tone == "ok" and res.lines[0] == "خروج مبكر بـ 10 دقيقة"


def test_kiosk_records_nothing_after_the_day_s_exit(db):
    switch_on(db)
    db.settings.set("telegram.punches", True)
    eid = add(db, "حسن")
    logic = KioskLogic(db)
    logic.register(eid, datetime(2026, 10, 25, 8, 0))
    db.clock.now = datetime(2026, 10, 25, 14, 50)
    assert logic.register(eid, db.clock.now).punch_type == "out"
    db.clock.now = datetime(2026, 10, 25, 15, 50)  # in front of the camera again an hour later
    res = logic.register(eid, db.clock.now)
    assert (res.kind, res.title) == ("left", "انصرافك مسجّل اليوم")
    assert res.lines == ["سُجّل انصرافك الساعة 14:50", "لا حاجة إلى بصمة أخرى"]
    db.clock.now = datetime(2026, 10, 25, 17, 30)  # after the last exit time: not a security event
    assert logic.register(eid, db.clock.now).kind == "left"
    assert db.scalar("SELECT COUNT(*) FROM punches WHERE employee_id = ?", (eid,)) == 2
    assert db.scalar("SELECT COUNT(*) FROM outbox") == 2
    assert db.scalar("SELECT COUNT(*) FROM security_events") == 0
    # an early exit can still be followed by the real one
    other = add(db, "ليلى")
    for hhmm in ("08:00", "14:35", "14:50"):
        db.clock.now = datetime.fromisoformat(f"2026-10-25 {hhmm}")
        assert logic.register(other, db.clock.now).kind == "punched"
    db.clock.now = datetime(2026, 10, 25, 15, 0)
    assert logic.register(other, db.clock.now).kind == "left"


def test_hourly_leave_until_the_end_of_the_day_is_not_an_absence(db):
    from hadir.core import engine as E

    eid = add(db, "نور")
    logic = KioskLogic(db)
    logic.register(eid, datetime(2026, 10, 25, 8, 0))
    db.clock.now = datetime(2026, 10, 25, 13, 0)  # leaves at 13:00 on a two-hour leave
    assert logic.register(eid, db.clock.now).kind == "early"
    hourly = db.scalar("SELECT id FROM leave_types WHERE unit = 'hour'")
    db.leaves.add(eid, hourly, "2026-10-25", start_time="13:00", end_time="15:00")  # entered afterwards
    db.clock.now = datetime(2026, 10, 25, 17, 30)
    [(_, [rec])] = db.compute([eid], date(2026, 10, 25), date(2026, 10, 25))
    assert rec.status == E.PRESENT and "left_on_leave" in rec.flags
    # recorded beforehand, the exit punch is taken at the start of the leave
    other = add(db, "رنا")
    db.leaves.add(other, hourly, "2026-10-26", start_time="13:00", end_time="15:00")
    db.clock.now = datetime(2026, 10, 26, 8, 0)
    logic.register(other, db.clock.now)
    db.clock.now = datetime(2026, 10, 26, 13, 0)
    res = logic.register(other, db.clock.now)
    assert res.kind == "punched" and res.punch_type == "out" and res.tone == "ok"
    db.clock.now = datetime(2026, 10, 26, 14, 50)
    assert logic.register(other, db.clock.now).kind == "left"


def test_real_http_round_trip(monkeypatch):
    """The request format and reply parsing, against a local stand-in for the Bot API."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            ctype = self.headers["Content-Type"]
            if ctype.startswith("multipart/form-data"):
                from email import policy
                from email.parser import BytesParser

                msg = BytesParser(policy=policy.default).parsebytes(
                    b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
                body = {}
                for part in msg.iter_parts():
                    data = part.get_payload(decode=True)
                    name = part.get_param("name", header="content-disposition")
                    body[name] = data if part.get_filename() else data.decode("utf-8")
            else:
                body = json.loads(raw.decode())
            seen.append((self.path, ctype, body))
            ok = body.get("chat_id") != "-404"
            reply = {"ok": True, "result": {"message_id": 1}} if ok else {
                "ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
            data = json.dumps(reply).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    for var in ("no_proxy", "NO_PROXY"):  # never send the local test request through a proxy
        monkeypatch.setenv(var, "127.0.0.1,localhost")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(T, "API", f"http://127.0.0.1:{server.server_port}/bot{{token}}/{{method}}")
        T.send_message("123:ABC", "-100", "مرحباً")
        path, ctype, body = seen[0]
        assert path == "/bot123:ABC/sendMessage" and ctype == "application/json"
        assert body["chat_id"] == "-100" and body["text"] == "مرحباً"
        with pytest.raises(T.TelegramError) as exc:
            T.send_message("123:ABC", "-404", "x")
        assert exc.value.permanent and "المجموعة" in str(exc.value)
        T.deliver("123:ABC", "-100", "<b>ملخص الدوام</b>", b"\x89PNG fake picture", html=True)
        path, ctype, body = seen[-1]
        assert path == "/bot123:ABC/sendPhoto" and ctype.startswith("multipart/form-data; boundary=")
        assert body == {"chat_id": "-100", "caption": "<b>ملخص الدوام</b>", "parse_mode": "HTML",
                        "photo": b"\x89PNG fake picture"}
    finally:
        server.shutdown()
