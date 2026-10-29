"""Telegram notifications and the kiosk's strict-rule behaviour (no network is used)."""

import io
import json
import urllib.error
from datetime import date, datetime

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
        self.fail = list(fail or [])

    def __call__(self, token, chat, text):
        if self.fail:
            raise self.fail.pop(0)
        self.sent.append((token, chat, text))


# ---------------------------------------------------------------- messages
def test_punch_messages_carry_name_job_and_times(db):
    switch_on(db)
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


def test_daily_list_by_name_with_status_and_summary(db):
    a, b, c = add(db, "باسم"), add(db, "أحمد", "مهندس"), add(db, "زينب")
    db.punches.add_face(a, datetime(2026, 10, 25, 8, 0))
    db.punches.add_face(a, datetime(2026, 10, 25, 14, 50))
    db.punches.add_face(b, datetime(2026, 10, 25, 8, 40))
    db.punches.add_face(b, datetime(2026, 10, 25, 14, 46))
    switch_on(db)
    db.clock.now = datetime(2026, 10, 25, 17, 5)
    [text] = T.daily_list(db, date(2026, 10, 25))
    lines = text.splitlines()
    assert lines[0] == "📋 قائمة الحضور — الأحد 2026/10/25" and lines[1] == "دائرة الاختبار"
    assert lines[3] == "1. أحمد — مهندس — دخول 08:40 — خروج 14:46 — متأخر 40 دقيقة"
    assert lines[4] == "2. باسم — محاسب — دخول 08:00 — خروج 14:50 — حاضر"
    assert lines[5] == "3. زينب — محاسب — غائب"
    assert lines[-1] == "الحاضرون: 2 • المتأخرون: 1 • الغائبون: 1 • المجازون: 0"
    assert T.daily_list(db, date(2026, 10, 30)) == []  # Friday: nobody works
    assert c


def test_long_lists_are_split(db, monkeypatch):
    for i in range(30):
        add(db, f"موظف رقم {i:02d} باسم طويل نوعاً ما")
    monkeypatch.setattr(T, "MAX_TEXT", 600)
    messages = T.daily_list(db, date(2026, 10, 25))
    assert len(messages) > 1 and all(len(m) <= 600 for m in messages)
    assert sum(m.count("موظف رقم") for m in messages) == 30


def test_daily_list_is_due_once_after_its_time(db):
    switch_on(db)
    assert not T.daily_list_due(db, datetime(2026, 10, 25, 16, 0))
    assert T.daily_list_due(db, datetime(2026, 10, 25, 17, 6))
    db.settings.set("telegram.last_list", "2026-10-25", audit=False)
    assert not T.daily_list_due(db, datetime(2026, 10, 25, 18, 0))


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


def test_kiosk_early_exit_is_flagged(db):
    eid = add(db, "مريم")
    logic = KioskLogic(db)
    logic.register(eid, datetime(2026, 10, 25, 8, 5))
    db.clock.now = datetime(2026, 10, 25, 13, 0)
    res = logic.register(eid, datetime(2026, 10, 25, 13, 0))
    assert res.punch_type == "out" and res.tone == "warn" and "قبل الساعة 14:45" in res.lines[0]


def test_real_http_round_trip(monkeypatch):
    """The request format and reply parsing, against a local stand-in for the Bot API."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
            seen.append((self.path, self.headers["Content-Type"], body))
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
    finally:
        server.shutdown()
