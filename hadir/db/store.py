"""Data access layer on top of SQLite.

All writes made by a signed-in user are recorded in the audit log.  Punches
are never removed: corrections soft-delete, restore, add or move them and each
correction carries a mandatory written reason in the append-only
``corrections`` table.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np

from ..core import engine as E
from ..core.security import hash_password, verify_password
from ..core.settings import DEFAULTS
from ..core.timeutil import iso_dt, parse_date, parse_dt, parse_time
from .schema import MIGRATIONS

MIN_REASON = 3


class StoreError(Exception):
    """A user-facing error (message is Arabic)."""


@dataclass
class User:
    id: int
    username: str
    full_name: str
    role: str


def _d(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def _t(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, time):
        return v.strftime("%H:%M")
    return str(v)[:5]


class Database:
    def __init__(self, path: str | Path, clock=datetime.now):
        self.path = str(path)
        self.clock = clock
        self.user: User | None = None
        self._lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False,
                                    timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self._migrate()
        self.settings = SettingsRepo(self)
        self.users = UsersRepo(self)
        self.groups = GroupsRepo(self)
        self.schedules = SchedulesRepo(self)
        self.employees = EmployeesRepo(self)
        self.faces = FacesRepo(self)
        self.leaves = LeavesRepo(self)
        self.holidays = HolidaysRepo(self)
        self.punches = PunchesRepo(self)
        self.events = EventsRepo(self)
        self.audit_log = AuditRepo(self)
        self.templates = TemplatesRepo(self)

    # ------------------------------------------------------------------ core
    def _migrate(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.tx():
                for stmt in _split_sql(sql):
                    self.conn.execute(stmt)
                self.conn.execute(f"PRAGMA user_version = {i}")

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    @contextmanager
    def tx(self):
        with self._lock:
            if self._depth == 0:
                self.conn.execute("BEGIN IMMEDIATE")
            else:
                self.conn.execute(f"SAVEPOINT sp{self._depth}")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("ROLLBACK")
                else:
                    self.conn.execute(f"ROLLBACK TO sp{self._depth}")
                    self.conn.execute(f"RELEASE sp{self._depth}")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self.conn.execute("COMMIT")
                else:
                    self.conn.execute(f"RELEASE sp{self._depth}")

    def q(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params=()):
        row = self.one(sql, params)
        return None if row is None else row[0]

    def x(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def now(self) -> datetime:
        return self.clock().replace(microsecond=0)

    def now_s(self) -> str:
        return iso_dt(self.now())

    def audit(self, action: str, entity: str = "", entity_id=None, details="") -> None:
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False, default=str)
        u = self.user
        self.x("INSERT INTO audit_log (ts, user_id, username, action, entity, entity_id, details)"
               " VALUES (?,?,?,?,?,?,?)",
               (self.now_s(), u.id if u else None, u.username if u else "", action, entity,
                entity_id, details))

    def close(self):
        with self._lock:
            self.conn.close()

    def backup_to(self, target: str | Path) -> None:
        dest = sqlite3.connect(str(target))
        try:
            with self._lock:
                self.conn.backup(dest)
        finally:
            dest.close()

    # ------------------------------------------------------ engine loading
    def load_org(self) -> E.OrgData:
        s = self.settings
        try:
            day_start = parse_time(s.get("attendance.day_start")) or time(4, 0)
        except (TypeError, ValueError):
            day_start = time(4, 0)
        org = E.OrgData(
            schedules=self.schedules.all_engine(),
            holidays=self.holidays.all_engine(),
            day_start=day_start,
            dedupe_seconds=int(s.get("attendance.dedupe_seconds")),
            low_liveness=float(s.get("attendance.low_liveness") or 0),
        )
        for r in self.q("SELECT * FROM schedule_assignments WHERE group_id IS NOT NULL"):
            org.group_assignments.setdefault(r["group_id"], []).append(
                E.Assignment(r["schedule_id"], parse_date(r["start_date"]), parse_date(r["end_date"])))
        return org

    def load_employees(self, ids: list[int] | None, start: date, end: date) -> list[E.EmployeeData]:
        """Load everything the engine needs for these employees and dates."""
        if ids is None:
            rows = self.q("SELECT * FROM employees ORDER BY full_name")
        else:
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            rows = self.q(f"SELECT * FROM employees WHERE id IN ({marks}) ORDER BY full_name", ids)
        ids = [r["id"] for r in rows]
        out = {r["id"]: E.EmployeeData(
            r["id"], r["full_name"], r["code"], parse_date(r["hire_date"]), parse_date(r["end_date"]))
            for r in rows}
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        for r in self.q(f"SELECT * FROM employee_groups WHERE employee_id IN ({marks})", ids):
            out[r["employee_id"]].groups.append(
                E.GroupPeriod(r["group_id"], parse_date(r["start_date"]), parse_date(r["end_date"])))
        for r in self.q(f"SELECT * FROM schedule_assignments WHERE employee_id IN ({marks})", ids):
            out[r["employee_id"]].assignments.append(
                E.Assignment(r["schedule_id"], parse_date(r["start_date"]), parse_date(r["end_date"])))
        types = self.leaves.types_engine()
        lo, hi = (start - timedelta(days=2)).isoformat(), (end + timedelta(days=2)).isoformat()
        for r in self.q(f"SELECT * FROM leaves WHERE cancelled = 0 AND employee_id IN ({marks})"
                        " AND start_date <= ? AND end_date >= ?", ids + [hi, lo]):
            out[r["employee_id"]].leaves.append(E.Leave(
                r["id"], types[r["leave_type_id"]], parse_date(r["start_date"]),
                parse_date(r["end_date"]), parse_time(r["start_time"]), parse_time(r["end_time"]),
                r["note"]))
        corrected = {r[0] for r in self.q(
            f"SELECT DISTINCT punch_id FROM corrections WHERE employee_id IN ({marks})", ids)}
        lo_ts = (start - timedelta(days=2)).isoformat() + " 00:00:00"
        hi_ts = (end + timedelta(days=3)).isoformat() + " 00:00:00"
        for r in self.q(f"SELECT * FROM punches WHERE deleted = 0 AND employee_id IN ({marks})"
                        " AND ts >= ? AND ts < ? ORDER BY ts", ids + [lo_ts, hi_ts]):
            out[r["employee_id"]].punches.append(E.Punch(
                r["id"], parse_dt(r["ts"]), r["kind"], r["source"], r["id"] in corrected,
                r["score"], r["liveness"]))
        return [out[i] for i in ids]

    def compute(self, ids: list[int] | None, start: date, end: date, now: datetime | None = None):
        """Daily records for employees in [start, end] -> list of (EmployeeData, [DayRecord])."""
        org = self.load_org()
        if now is None:
            now = self.now()
        return [(e, E.compute_days(e, org, start, end, now=now))
                for e in self.load_employees(ids, start, end)]


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies intact."""
    out, buf = [], []
    depth = 0
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.startswith("CREATE TRIGGER"):
            depth += 1
        if stripped.endswith(";"):
            if depth and "END;" not in upper:
                continue
            depth = 0
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


class _Repo:
    def __init__(self, db: Database):
        self.db = db


# ---------------------------------------------------------------------------
class SettingsRepo(_Repo):
    def get(self, key: str):
        row = self.db.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return DEFAULTS.get(key)
        return json.loads(row[0])

    def all(self) -> dict:
        out = dict(DEFAULTS)
        for r in self.db.q("SELECT key, value FROM settings"):
            out[r[0]] = json.loads(r[1])
        return out

    def set(self, key: str, value, audit: bool = True) -> None:
        old = self.get(key)
        self.db.x("INSERT INTO settings (key, value) VALUES (?, ?)"
                  " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))
        if audit and old != value:
            shown = "(صورة)" if key == "org.logo" else value
            self.db.audit("تعديل إعداد", "settings", None, {"key": key, "value": shown})

    def set_many(self, values: dict) -> None:
        with self.db.tx():
            for k, v in values.items():
                self.set(k, v)


# ---------------------------------------------------------------------------
class UsersRepo(_Repo):
    def count(self) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM users")

    def list(self):
        return self.db.q("SELECT id, username, full_name, role, active, created_at, last_login"
                         " FROM users ORDER BY username")

    def create(self, username: str, full_name: str, password: str, role: str) -> int:
        username = username.strip()
        if not username:
            raise StoreError("اسم المستخدم مطلوب.")
        if len(password) < 6:
            raise StoreError("يجب ألّا تقل كلمة المرور عن ٦ أحرف.")
        if self.db.one("SELECT 1 FROM users WHERE username = ?", (username,)):
            raise StoreError("اسم المستخدم مستعمل مسبقاً.")
        with self.db.tx():
            cur = self.db.x("INSERT INTO users (username, full_name, password_hash, role, created_at)"
                            " VALUES (?,?,?,?,?)",
                            (username, full_name.strip(), hash_password(password), role,
                             self.db.now_s()))
            self.db.audit("إضافة مستخدم", "user", cur.lastrowid, {"username": username, "role": role})
        return cur.lastrowid

    def authenticate(self, username: str, password: str) -> User | None:
        row = self.db.one("SELECT * FROM users WHERE username = ? AND active = 1", (username.strip(),))
        if row is None or not verify_password(password, row["password_hash"]):
            self.db.events.add("login_failed", f"محاولة دخول فاشلة باسم: {username}")
            return None
        user = User(row["id"], row["username"], row["full_name"], row["role"])
        self.db.x("UPDATE users SET last_login = ? WHERE id = ?", (self.db.now_s(), row["id"]))
        self.db.user = user
        self.db.audit("تسجيل دخول", "user", row["id"])
        return user

    def logout(self) -> None:
        if self.db.user:
            self.db.audit("تسجيل خروج", "user", self.db.user.id)
        self.db.user = None

    def set_password(self, user_id: int, password: str) -> None:
        if len(password) < 6:
            raise StoreError("يجب ألّا تقل كلمة المرور عن ٦ أحرف.")
        with self.db.tx():
            self.db.x("UPDATE users SET password_hash = ? WHERE id = ?",
                      (hash_password(password), user_id))
            self.db.audit("تغيير كلمة المرور", "user", user_id)

    def update(self, user_id: int, full_name: str, role: str, active: bool) -> None:
        with self.db.tx():
            row = self.db.one("SELECT role, active FROM users WHERE id = ?", (user_id,))
            if row and row["role"] == "admin" and (role != "admin" or not active):
                others = self.db.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1"
                                        " AND id <> ?", (user_id,))
                if not others:
                    raise StoreError("لا يمكن إلغاء صلاحية آخر مدير للنظام.")
            self.db.x("UPDATE users SET full_name = ?, role = ?, active = ? WHERE id = ?",
                      (full_name, role, int(active), user_id))
            self.db.audit("تعديل مستخدم", "user", user_id, {"role": role, "active": active})


# ---------------------------------------------------------------------------
class GroupsRepo(_Repo):
    def list(self, include_inactive: bool = False):
        sql = ("SELECT g.*, (SELECT COUNT(*) FROM employee_groups eg JOIN employees e ON e.id ="
               " eg.employee_id WHERE eg.group_id = g.id AND eg.end_date IS NULL AND e.active = 1)"
               " AS members FROM emp_groups g")
        if not include_inactive:
            sql += " WHERE g.active = 1"
        return self.db.q(sql + " ORDER BY g.name")

    def names(self) -> dict[int, str]:
        return {r["id"]: r["name"] for r in self.db.q("SELECT id, name FROM emp_groups")}

    def save(self, name: str, description: str = "", group_id: int | None = None,
             active: bool = True) -> int:
        name = name.strip()
        if not name:
            raise StoreError("اسم المجموعة مطلوب.")
        dup = self.db.one("SELECT id FROM emp_groups WHERE name = ? AND id <> ?", (name, group_id or 0))
        if dup:
            raise StoreError("توجد مجموعة بهذا الاسم.")
        with self.db.tx():
            if group_id is None:
                cur = self.db.x("INSERT INTO emp_groups (name, description, active, created_at)"
                                " VALUES (?,?,?,?)", (name, description, int(active), self.db.now_s()))
                group_id = cur.lastrowid
                self.db.audit("إضافة مجموعة", "group", group_id, name)
            else:
                self.db.x("UPDATE emp_groups SET name = ?, description = ?, active = ? WHERE id = ?",
                          (name, description, int(active), group_id))
                self.db.audit("تعديل مجموعة", "group", group_id, name)
        return group_id

    def delete(self, group_id: int) -> None:
        used = self.db.scalar("SELECT COUNT(*) FROM employee_groups WHERE group_id = ?", (group_id,))
        if used:
            raise StoreError("لا يمكن حذف مجموعة لها موظفون (حاليون أو سابقون). يمكنك تعطيلها بدلاً من ذلك.")
        with self.db.tx():
            self.db.x("DELETE FROM schedule_assignments WHERE group_id = ?", (group_id,))
            self.db.x("DELETE FROM emp_groups WHERE id = ?", (group_id,))
            self.db.audit("حذف مجموعة", "group", group_id)

    def assignments(self, group_id: int):
        return self.db.q("SELECT a.*, s.name AS schedule_name FROM schedule_assignments a"
                         " JOIN schedules s ON s.id = a.schedule_id WHERE a.group_id = ?"
                         " ORDER BY a.start_date DESC", (group_id,))

    def current_schedule(self, group_id: int, on: date | None = None):
        on = on or self.db.now().date()
        return self.db.one(
            "SELECT s.* FROM schedule_assignments a JOIN schedules s ON s.id = a.schedule_id"
            " WHERE a.group_id = ? AND a.start_date <= ? AND (a.end_date IS NULL OR a.end_date >= ?)"
            " ORDER BY a.start_date DESC, a.id DESC LIMIT 1", (group_id, on.isoformat(), on.isoformat()))

    def assign_schedule(self, group_id: int, schedule_id: int, start, end=None, note: str = "") -> int:
        return self.db.schedules.assign(schedule_id, start, end, group_id=group_id, note=note)


# ---------------------------------------------------------------------------
class SchedulesRepo(_Repo):
    def list(self, include_inactive: bool = True):
        sql = "SELECT * FROM schedules"
        if not include_inactive:
            sql += " WHERE active = 1"
        return self.db.q(sql + " ORDER BY name")

    def days(self, schedule_id: int):
        return self.db.q("SELECT * FROM schedule_days WHERE schedule_id = ? ORDER BY day_index",
                         (schedule_id,))

    def _engine(self, row, days) -> E.Schedule:
        rules = tuple(E.DayRule(bool(d["workday"]), parse_time(d["start_time"]), parse_time(d["end_time"]))
                      for d in days)
        return E.Schedule(row["id"], row["name"], row["kind"], rules, parse_date(row["cycle_anchor"]),
                          row["grace_late"], row["grace_early"], bool(row["late_after_grace"]),
                          row["window_before"], row["window_after"], row["overtime_min"])

    def get(self, schedule_id: int) -> E.Schedule | None:
        row = self.db.one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        return None if row is None else self._engine(row, self.days(schedule_id))

    def all_engine(self) -> dict[int, E.Schedule]:
        days: dict[int, list] = {}
        for d in self.db.q("SELECT * FROM schedule_days ORDER BY schedule_id, day_index"):
            days.setdefault(d["schedule_id"], []).append(d)
        return {r["id"]: self._engine(r, days.get(r["id"], []))
                for r in self.db.q("SELECT * FROM schedules")}

    def save(self, data: dict, schedule_id: int | None = None) -> int:
        """data: name, kind, cycle_anchor, grace_late, grace_early, late_after_grace,
        window_before, window_after, overtime_min, notes, active,
        days: [(workday, start 'HH:MM', end 'HH:MM'), ...]"""
        name = (data.get("name") or "").strip()
        if not name:
            raise StoreError("اسم الجدول مطلوب.")
        days = data.get("days") or []
        kind = data.get("kind", "weekly")
        if kind == "weekly" and len(days) != 7:
            raise StoreError("الجدول الأسبوعي يحتاج سبعة أيام.")
        if kind == "cycle" and not (1 <= len(days) <= 62):
            raise StoreError("طول الدورة يجب أن يكون بين ١ و٦٢ يوماً.")
        if kind == "cycle" and not data.get("cycle_anchor"):
            raise StoreError("حدّد تاريخ بداية الدورة.")
        for wd, st, en in days:
            if wd and (not st or not en):
                raise StoreError("حدّد وقت البداية والنهاية لكل يوم دوام.")
        if not any(wd for wd, _, _ in days):
            raise StoreError("يجب أن يحتوي الجدول على يوم دوام واحد على الأقل.")
        dup = self.db.one("SELECT id FROM schedules WHERE name = ? AND id <> ?", (name, schedule_id or 0))
        if dup:
            raise StoreError("يوجد جدول بهذا الاسم.")
        fields = ("name", "kind", "cycle_anchor", "grace_late", "grace_early", "late_after_grace",
                  "window_before", "window_after", "overtime_min", "notes", "active")
        defaults = {"cycle_anchor": None, "grace_late": 10, "grace_early": 0, "late_after_grace": 0,
                    "window_before": 240, "window_after": 480, "overtime_min": 30, "notes": "",
                    "active": 1}
        values = {f: data.get(f, defaults.get(f)) for f in fields}
        values["name"] = name
        values["cycle_anchor"] = _d(values["cycle_anchor"]) if kind == "cycle" else None
        for f in ("late_after_grace", "active"):
            values[f] = int(bool(values[f]))
        with self.db.tx():
            if schedule_id is None:
                cur = self.db.x(
                    f"INSERT INTO schedules ({','.join(fields)}, created_at) VALUES"
                    f" ({','.join('?' * len(fields))}, ?)",
                    [values[f] for f in fields] + [self.db.now_s()])
                schedule_id = cur.lastrowid
                self.db.audit("إضافة جدول دوام", "schedule", schedule_id, name)
            else:
                self.db.x(f"UPDATE schedules SET {','.join(f + ' = ?' for f in fields)} WHERE id = ?",
                          [values[f] for f in fields] + [schedule_id])
                self.db.x("DELETE FROM schedule_days WHERE schedule_id = ?", (schedule_id,))
                self.db.audit("تعديل جدول دوام", "schedule", schedule_id, name)
            for i, (wd, st, en) in enumerate(days):
                self.db.x("INSERT INTO schedule_days (schedule_id, day_index, workday, start_time,"
                          " end_time) VALUES (?,?,?,?,?)",
                          (schedule_id, i, int(bool(wd)), _t(st) if wd else None, _t(en) if wd else None))
        return schedule_id

    def usage(self, schedule_id: int) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM schedule_assignments WHERE schedule_id = ?",
                              (schedule_id,))

    def delete(self, schedule_id: int) -> None:
        if self.usage(schedule_id):
            raise StoreError("الجدول مستخدم في إسنادات؛ احذف الإسنادات أولاً أو عطّل الجدول.")
        with self.db.tx():
            self.db.x("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            self.db.audit("حذف جدول دوام", "schedule", schedule_id)

    def assign(self, schedule_id: int, start, end=None, *, group_id: int | None = None,
               employee_id: int | None = None, note: str = "") -> int:
        start, end = _d(start), _d(end)
        if not start:
            raise StoreError("حدّد تاريخ بداية الإسناد.")
        if end and end < start:
            raise StoreError("تاريخ النهاية قبل تاريخ البداية.")
        with self.db.tx():
            cur = self.db.x("INSERT INTO schedule_assignments (schedule_id, group_id, employee_id,"
                            " start_date, end_date, note, created_at) VALUES (?,?,?,?,?,?,?)",
                            (schedule_id, group_id, employee_id, start, end, note, self.db.now_s()))
            self.db.audit("إسناد جدول دوام", "group" if group_id else "employee",
                          group_id or employee_id,
                          {"schedule_id": schedule_id, "from": start, "to": end})
        return cur.lastrowid

    def employee_assignments(self, employee_id: int):
        return self.db.q("SELECT a.*, s.name AS schedule_name FROM schedule_assignments a"
                         " JOIN schedules s ON s.id = a.schedule_id WHERE a.employee_id = ?"
                         " ORDER BY a.start_date DESC", (employee_id,))

    def delete_assignment(self, assignment_id: int) -> None:
        with self.db.tx():
            self.db.x("DELETE FROM schedule_assignments WHERE id = ?", (assignment_id,))
            self.db.audit("حذف إسناد جدول", "assignment", assignment_id)


# ---------------------------------------------------------------------------
class EmployeesRepo(_Repo):
    BASE = ("SELECT e.*, eg.group_id, g.name AS group_name,"
            " (SELECT COUNT(*) FROM face_samples f WHERE f.employee_id = e.id) AS samples"
            " FROM employees e"
            " LEFT JOIN employee_groups eg ON eg.employee_id = e.id AND eg.end_date IS NULL"
            " LEFT JOIN emp_groups g ON g.id = eg.group_id")

    def list(self, search: str = "", group_id: int | None = None, active: bool | None = True):
        where, params = [], []
        if search:
            where.append("(e.full_name LIKE ? OR e.code LIKE ? OR e.job_title LIKE ?"
                         " OR e.department LIKE ?)")
            params += [f"%{search}%"] * 4
        if group_id is not None:
            where.append("eg.group_id = ?")
            params.append(group_id)
        if active is not None:
            where.append("e.active = ?")
            params.append(int(active))
        sql = self.BASE + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.full_name"
        return self.db.q(sql, params)

    def get(self, employee_id: int):
        return self.db.one(self.BASE + " WHERE e.id = ?", (employee_id,))

    def by_code(self, code: str):
        return self.db.one(self.BASE + " WHERE e.code = ?", (code.strip(),))

    def names(self) -> dict[int, str]:
        return {r["id"]: r["full_name"] for r in self.db.q("SELECT id, full_name FROM employees")}

    def next_code(self) -> str:
        codes = [r[0] for r in self.db.q("SELECT code FROM employees")]
        nums = [int(c) for c in codes if c.isdigit()]
        return str(max(nums) + 1 if nums else 1001)

    def save(self, data: dict, employee_id: int | None = None, group_effective=None) -> int:
        name = (data.get("full_name") or "").strip()
        code = (data.get("code") or "").strip()
        if not name:
            raise StoreError("اسم الموظف مطلوب.")
        if not code:
            raise StoreError("الرقم الوظيفي مطلوب.")
        dup = self.db.one("SELECT id FROM employees WHERE code = ? AND id <> ?", (code, employee_id or 0))
        if dup:
            raise StoreError("الرقم الوظيفي مستخدم لموظف آخر.")
        hire, end = _d(data.get("hire_date")), _d(data.get("end_date"))
        if hire and end and end < hire:
            raise StoreError("تاريخ ترك الخدمة قبل تاريخ المباشرة.")
        fields = ("code", "full_name", "job_title", "department", "phone", "notes", "hire_date",
                  "end_date", "active")
        values = {
            "code": code, "full_name": name,
            "job_title": (data.get("job_title") or "").strip(),
            "department": (data.get("department") or "").strip(),
            "phone": (data.get("phone") or "").strip(),
            "notes": data.get("notes") or "",
            "hire_date": hire, "end_date": end,
            "active": int(bool(data.get("active", True))),
        }
        now = self.db.now_s()
        with self.db.tx():
            if employee_id is None:
                cur = self.db.x(f"INSERT INTO employees ({','.join(fields)}, created_at, updated_at)"
                                f" VALUES ({','.join('?' * len(fields))}, ?, ?)",
                                [values[f] for f in fields] + [now, now])
                employee_id = cur.lastrowid
                self.db.audit("إضافة موظف", "employee", employee_id, f"{code} - {name}")
            else:
                old = self.db.one("SELECT * FROM employees WHERE id = ?", (employee_id,))
                self.db.x(f"UPDATE employees SET {','.join(f + ' = ?' for f in fields)},"
                          " updated_at = ? WHERE id = ?",
                          [values[f] for f in fields] + [now, employee_id])
                changes = {f: [old[f], values[f]] for f in fields if old[f] != values[f]}
                if changes:
                    self.db.audit("تعديل موظف", "employee", employee_id, changes)
            if "group_id" in data:
                self.set_group(employee_id, data["group_id"], group_effective or hire or "2000-01-01")
            if "photo" in data and data["photo"] is not None:
                self.set_photo(employee_id, data["photo"])
        return employee_id

    def set_photo(self, employee_id: int, jpeg: bytes | None) -> None:
        self.db.x("UPDATE employees SET photo = ? WHERE id = ?", (jpeg, employee_id))

    def photo(self, employee_id: int) -> bytes | None:
        return self.db.scalar("SELECT photo FROM employees WHERE id = ?", (employee_id,))

    def group_history(self, employee_id: int):
        return self.db.q("SELECT eg.*, g.name FROM employee_groups eg JOIN emp_groups g ON"
                         " g.id = eg.group_id WHERE eg.employee_id = ? ORDER BY eg.start_date DESC",
                         (employee_id,))

    def set_group(self, employee_id: int, group_id: int | None, effective) -> None:
        """Move the employee to ``group_id`` from ``effective`` (history is kept)."""
        effective = _d(effective)
        with self.db.tx():
            current = self.db.one("SELECT * FROM employee_groups WHERE employee_id = ? AND end_date"
                                  " IS NULL", (employee_id,))
            if current and current["group_id"] == group_id:
                return
            if current:
                if current["start_date"] >= effective:
                    # Same-day correction: replace instead of creating a zero-length period.
                    self.db.x("DELETE FROM employee_groups WHERE id = ?", (current["id"],))
                    prev = self.db.one("SELECT * FROM employee_groups WHERE employee_id = ? AND"
                                       " end_date IS NOT NULL ORDER BY end_date DESC LIMIT 1",
                                       (employee_id,))
                    if prev and prev["group_id"] == group_id and prev["end_date"] >= _d(
                            parse_date(effective) - timedelta(days=1)):
                        self.db.x("UPDATE employee_groups SET end_date = NULL WHERE id = ?",
                                  (prev["id"],))
                        group_id = None
                else:
                    end = parse_date(effective) - timedelta(days=1)
                    self.db.x("UPDATE employee_groups SET end_date = ? WHERE id = ?",
                              (end.isoformat(), current["id"]))
            if group_id is not None:
                self.db.x("INSERT INTO employee_groups (employee_id, group_id, start_date)"
                          " VALUES (?,?,?)", (employee_id, group_id, effective))
            self.db.audit("نقل موظف إلى مجموعة", "employee", employee_id,
                          {"group_id": group_id, "from": effective})

    def delete(self, employee_id: int) -> None:
        if self.db.scalar("SELECT COUNT(*) FROM punches WHERE employee_id = ?", (employee_id,)):
            raise StoreError("لا يمكن حذف موظف له سجل دوام. اجعله «غير فعّال» أو حدّد تاريخ ترك الخدمة.")
        with self.db.tx():
            row = self.db.one("SELECT code, full_name FROM employees WHERE id = ?", (employee_id,))
            self.db.x("DELETE FROM employees WHERE id = ?", (employee_id,))
            self.db.audit("حذف موظف", "employee", employee_id,
                          f"{row['code']} - {row['full_name']}" if row else "")

    def set_pin(self, employee_id: int, pin: str | None) -> None:
        h = hash_password(pin, iterations=60_000) if pin else None
        with self.db.tx():
            self.db.x("UPDATE employees SET pin_hash = ? WHERE id = ?", (h, employee_id))
            self.db.audit("تعيين رمز سري للموظف", "employee", employee_id)

    def check_pin(self, code: str, pin: str):
        row = self.db.one("SELECT * FROM employees WHERE code = ? AND active = 1", (code.strip(),))
        if row and row["pin_hash"] and verify_password(pin, row["pin_hash"]):
            return row
        return None


# ---------------------------------------------------------------------------
class FacesRepo(_Repo):
    def add_sample(self, employee_id: int, chip_png: bytes, quality: float, pose: str,
                   source: str, embeddings: dict[str, np.ndarray]) -> int:
        with self.db.tx():
            cur = self.db.x("INSERT INTO face_samples (employee_id, chip, quality, pose, source,"
                            " created_at) VALUES (?,?,?,?,?,?)",
                            (employee_id, chip_png, float(quality), pose, source, self.db.now_s()))
            sid = cur.lastrowid
            for model_id, vec in embeddings.items():
                self.set_embedding(sid, model_id, vec)
        return sid

    def set_embedding(self, sample_id: int, model_id: str, vec: np.ndarray) -> None:
        self.db.x("INSERT OR REPLACE INTO face_embeddings (sample_id, model_id, vector)"
                  " VALUES (?,?,?)", (sample_id, model_id, np.asarray(vec, np.float32).tobytes()))

    def samples(self, employee_id: int):
        return self.db.q("SELECT id, employee_id, chip, quality, pose, source, created_at FROM"
                         " face_samples WHERE employee_id = ? ORDER BY id", (employee_id,))

    def count(self, employee_id: int, source: str | None = None) -> int:
        if source:
            return self.db.scalar("SELECT COUNT(*) FROM face_samples WHERE employee_id = ? AND"
                                  " source = ?", (employee_id, source))
        return self.db.scalar("SELECT COUNT(*) FROM face_samples WHERE employee_id = ?", (employee_id,))

    def delete_sample(self, sample_id: int) -> None:
        with self.db.tx():
            emp = self.db.scalar("SELECT employee_id FROM face_samples WHERE id = ?", (sample_id,))
            self.db.x("DELETE FROM face_samples WHERE id = ?", (sample_id,))
            self.db.audit("حذف عينة وجه", "employee", emp, {"sample": sample_id})

    def delete_all(self, employee_id: int) -> None:
        with self.db.tx():
            self.db.x("DELETE FROM face_samples WHERE employee_id = ?", (employee_id,))
            self.db.audit("حذف بصمة الوجه", "employee", employee_id)

    def oldest_adaptive(self, employee_id: int):
        return self.db.one("SELECT id FROM face_samples WHERE employee_id = ? AND source = 'adapt'"
                           " ORDER BY id LIMIT 1", (employee_id,))

    def last_adaptive_at(self, employee_id: int):
        return self.db.scalar("SELECT MAX(created_at) FROM face_samples WHERE employee_id = ? AND"
                              " source = 'adapt'", (employee_id,))

    def gallery(self, model_id: str, dim: int):
        """(sample_ids, employee_ids, matrix) for active employees."""
        rows = self.db.q("SELECT s.id, s.employee_id, v.vector FROM face_samples s"
                         " JOIN face_embeddings v ON v.sample_id = s.id AND v.model_id = ?"
                         " JOIN employees e ON e.id = s.employee_id WHERE e.active = 1"
                         " ORDER BY s.id", (model_id,))
        if not rows:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, dim), np.float32)
        sids = np.array([r[0] for r in rows], np.int64)
        eids = np.array([r[1] for r in rows], np.int64)
        mat = np.stack([np.frombuffer(r[2], np.float32) for r in rows])
        return sids, eids, mat

    def missing(self, model_id: str):
        return self.db.q("SELECT s.id, s.chip FROM face_samples s WHERE NOT EXISTS (SELECT 1 FROM"
                         " face_embeddings v WHERE v.sample_id = s.id AND v.model_id = ?)", (model_id,))

    def enrolled_count(self) -> int:
        return self.db.scalar("SELECT COUNT(DISTINCT s.employee_id) FROM face_samples s"
                              " JOIN employees e ON e.id = s.employee_id WHERE e.active = 1")


# ---------------------------------------------------------------------------
class LeavesRepo(_Repo):
    def types(self, include_inactive: bool = True):
        sql = "SELECT * FROM leave_types"
        if not include_inactive:
            sql += " WHERE active = 1"
        return self.db.q(sql + " ORDER BY id")

    def types_engine(self) -> dict[int, E.LeaveType]:
        return {r["id"]: E.LeaveType(r["id"], r["name"], r["unit"], bool(r["paid"]),
                                     bool(r["counts_present"]), bool(r["calendar_days"]),
                                     r["short"] or r["name"][:1])
                for r in self.db.q("SELECT * FROM leave_types")}

    def save_type(self, data: dict, type_id: int | None = None) -> int:
        name = (data.get("name") or "").strip()
        if not name:
            raise StoreError("اسم نوع الإجازة مطلوب.")
        dup = self.db.one("SELECT id FROM leave_types WHERE name = ? AND id <> ?", (name, type_id or 0))
        if dup:
            raise StoreError("يوجد نوع إجازة بهذا الاسم.")
        fields = ("name", "short", "unit", "paid", "counts_present", "calendar_days", "quota",
                  "quota_period", "color", "active")
        values = {
            "name": name, "short": (data.get("short") or name[:1]).strip(),
            "unit": data.get("unit", "day"), "paid": int(bool(data.get("paid", True))),
            "counts_present": int(bool(data.get("counts_present", False))),
            "calendar_days": int(bool(data.get("calendar_days", False))),
            "quota": data.get("quota"), "quota_period": data.get("quota_period", "year"),
            "color": data.get("color", "#3b82f6"), "active": int(bool(data.get("active", True))),
        }
        with self.db.tx():
            if type_id is None:
                cur = self.db.x(f"INSERT INTO leave_types ({','.join(fields)}) VALUES"
                                f" ({','.join('?' * len(fields))})", [values[f] for f in fields])
                type_id = cur.lastrowid
                self.db.audit("إضافة نوع إجازة", "leave_type", type_id, name)
            else:
                self.db.x(f"UPDATE leave_types SET {','.join(f + ' = ?' for f in fields)} WHERE id = ?",
                          [values[f] for f in fields] + [type_id])
                self.db.audit("تعديل نوع إجازة", "leave_type", type_id, name)
        return type_id

    def list(self, employee_id: int | None = None, start=None, end=None, type_id=None,
             include_cancelled: bool = False):
        where, params = [], []
        if employee_id:
            where.append("l.employee_id = ?")
            params.append(employee_id)
        if start:
            where.append("l.end_date >= ?")
            params.append(_d(start))
        if end:
            where.append("l.start_date <= ?")
            params.append(_d(end))
        if type_id:
            where.append("l.leave_type_id = ?")
            params.append(type_id)
        if not include_cancelled:
            where.append("l.cancelled = 0")
        sql = ("SELECT l.*, t.name AS type_name, t.unit, t.color, e.full_name, e.code FROM leaves l"
               " JOIN leave_types t ON t.id = l.leave_type_id JOIN employees e ON e.id = l.employee_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.q(sql + " ORDER BY l.start_date DESC, l.id DESC", params)

    def add(self, employee_id: int, type_id: int, start, end=None, start_time=None, end_time=None,
            reference: str = "", note: str = "") -> int:
        t = self.db.one("SELECT * FROM leave_types WHERE id = ?", (type_id,))
        if t is None:
            raise StoreError("نوع الإجازة غير موجود.")
        start = _d(start)
        end = _d(end) or start
        if not start:
            raise StoreError("حدّد تاريخ الإجازة.")
        if end < start:
            raise StoreError("تاريخ نهاية الإجازة قبل بدايتها.")
        if t["unit"] == "hour":
            if not start_time or not end_time:
                raise StoreError("الإجازة الزمنية تحتاج وقت البداية والنهاية.")
            end = start
            if _t(end_time) <= _t(start_time):
                raise StoreError("وقت نهاية الإجازة الزمنية يجب أن يكون بعد وقت بدايتها.")
            clash = self.db.one(
                "SELECT 1 FROM leaves l JOIN leave_types t ON t.id = l.leave_type_id WHERE"
                " l.employee_id = ? AND l.cancelled = 0 AND t.unit = 'hour' AND l.start_date = ?"
                " AND l.start_time < ? AND l.end_time > ?",
                (employee_id, start, _t(end_time), _t(start_time)))
        else:
            start_time = end_time = None
            clash = self.db.one(
                "SELECT 1 FROM leaves l JOIN leave_types t ON t.id = l.leave_type_id WHERE"
                " l.employee_id = ? AND l.cancelled = 0 AND t.unit = 'day' AND l.start_date <= ?"
                " AND l.end_date >= ?", (employee_id, end, start))
        if clash:
            raise StoreError("توجد إجازة أخرى متداخلة مع هذه الفترة لهذا الموظف.")
        with self.db.tx():
            cur = self.db.x(
                "INSERT INTO leaves (employee_id, leave_type_id, start_date, end_date, start_time,"
                " end_time, reference, note, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (employee_id, type_id, start, end, _t(start_time), _t(end_time), reference.strip(),
                 note.strip(), self.db.user.id if self.db.user else None, self.db.now_s()))
            self.db.audit("إضافة إجازة", "employee", employee_id,
                          {"type": t["name"], "from": start, "to": end,
                           "time": [_t(start_time), _t(end_time)] if start_time else None})
        return cur.lastrowid

    def cancel(self, leave_id: int, reason: str) -> None:
        if len((reason or "").strip()) < MIN_REASON:
            raise StoreError("اكتب سبب إلغاء الإجازة.")
        with self.db.tx():
            row = self.db.one("SELECT employee_id FROM leaves WHERE id = ?", (leave_id,))
            self.db.x("UPDATE leaves SET cancelled = 1, cancel_reason = ? WHERE id = ?",
                      (reason.strip(), leave_id))
            self.db.audit("إلغاء إجازة", "employee", row["employee_id"] if row else None,
                          {"leave": leave_id, "reason": reason.strip()})

    def balance(self, employee_id: int, year: int, month: int | None = None) -> list[dict]:
        """Used vs. quota per leave type for a year (or month for monthly quotas)."""
        out = []
        emp = self.db.load_employees([employee_id], date(year, 1, 1), date(year, 12, 31))
        if not emp:
            return out
        org = self.db.load_org()
        days = E.compute_days(emp[0], org, date(year, 1, 1), date(year, 12, 31), now=None)
        for t in self.types():
            used = 0.0
            for r in days:
                if month and r.date.month != month:
                    continue
                if t["unit"] == "day" and r.status == E.LEAVE and r.leave and r.leave.type.id == t["id"]:
                    used += 1
                if t["unit"] == "hour":
                    for lv in r.hourly_leaves:
                        if lv.type.id == t["id"]:
                            s, e = lv.interval()
                            used += (e - s).total_seconds() / 3600
            quota = t["quota"]
            if quota is not None and t["quota_period"] == "month" and not month:
                quota = quota * 12
            out.append({"type_id": t["id"], "name": t["name"], "unit": t["unit"], "used": used,
                        "quota": quota, "remaining": None if quota is None else quota - used})
        return out


# ---------------------------------------------------------------------------
class HolidaysRepo(_Repo):
    def list(self, year: int | None = None):
        rows = self.db.q("SELECT * FROM holidays" + (" WHERE start_date <= ? AND end_date >= ?"
                                                     if year else "") + " ORDER BY start_date",
                         (f"{year}-12-31", f"{year}-01-01") if year else ())
        groups: dict[int, list[int]] = {}
        for r in self.db.q("SELECT * FROM holiday_groups"):
            groups.setdefault(r["holiday_id"], []).append(r["group_id"])
        return [dict(r) | {"group_ids": groups.get(r["id"], [])} for r in rows]

    def all_engine(self) -> list[E.Holiday]:
        return [E.Holiday(h["id"], h["name"], parse_date(h["start_date"]), parse_date(h["end_date"]),
                          frozenset(h["group_ids"])) for h in self.list()]

    def save(self, name: str, start, end=None, group_ids=(), note: str = "",
             holiday_id: int | None = None) -> int:
        name = name.strip()
        start = _d(start)
        end = _d(end) or start
        if not name:
            raise StoreError("اسم العطلة مطلوب.")
        if not start:
            raise StoreError("حدّد تاريخ العطلة.")
        if end < start:
            raise StoreError("تاريخ نهاية العطلة قبل بدايتها.")
        with self.db.tx():
            if holiday_id is None:
                cur = self.db.x("INSERT INTO holidays (name, start_date, end_date, note, created_at)"
                                " VALUES (?,?,?,?,?)", (name, start, end, note, self.db.now_s()))
                holiday_id = cur.lastrowid
                self.db.audit("إضافة عطلة", "holiday", holiday_id, {"name": name, "from": start, "to": end})
            else:
                self.db.x("UPDATE holidays SET name = ?, start_date = ?, end_date = ?, note = ?"
                          " WHERE id = ?", (name, start, end, note, holiday_id))
                self.db.x("DELETE FROM holiday_groups WHERE holiday_id = ?", (holiday_id,))
                self.db.audit("تعديل عطلة", "holiday", holiday_id, {"name": name, "from": start, "to": end})
            for g in group_ids:
                self.db.x("INSERT INTO holiday_groups (holiday_id, group_id) VALUES (?,?)",
                          (holiday_id, g))
        return holiday_id

    def delete(self, holiday_id: int) -> None:
        with self.db.tx():
            row = self.db.one("SELECT name FROM holidays WHERE id = ?", (holiday_id,))
            self.db.x("DELETE FROM holidays WHERE id = ?", (holiday_id,))
            self.db.audit("حذف عطلة", "holiday", holiday_id, row["name"] if row else "")


# ---------------------------------------------------------------------------
class PunchesRepo(_Repo):
    def add_face(self, employee_id: int, ts: datetime, score: float | None = None,
                 liveness: float | None = None, snapshot: str | None = None,
                 source: str = "face") -> int:
        with self.db.tx():
            cur = self.db.x("INSERT INTO punches (employee_id, ts, original_ts, kind, source, score,"
                            " liveness, snapshot, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                            (employee_id, iso_dt(ts), iso_dt(ts), "auto", source, score, liveness,
                             snapshot, self.db.now_s()))
        return cur.lastrowid

    def set_snapshot(self, punch_id: int, path: str) -> None:
        self.db.x("UPDATE punches SET snapshot = ? WHERE id = ?", (path, punch_id))

    def get(self, punch_id: int):
        return self.db.one("SELECT * FROM punches WHERE id = ?", (punch_id,))

    def last_for(self, employee_id: int):
        return self.db.one("SELECT * FROM punches WHERE employee_id = ? AND deleted = 0"
                           " ORDER BY ts DESC LIMIT 1", (employee_id,))

    def list(self, employee_id: int | None = None, start=None, end=None,
             include_deleted: bool = False, source: str | None = None):
        where, params = [], []
        if employee_id:
            where.append("p.employee_id = ?")
            params.append(employee_id)
        if start:
            where.append("p.ts >= ?")
            params.append(_d(start) + " 00:00:00")
        if end:
            where.append("p.ts < ?")
            params.append((parse_date(_d(end)) + timedelta(days=1)).isoformat() + " 00:00:00")
        if not include_deleted:
            where.append("p.deleted = 0")
        if source:
            where.append("p.source = ?")
            params.append(source)
        sql = ("SELECT p.*, e.full_name, e.code, (SELECT COUNT(*) FROM corrections c WHERE"
               " c.punch_id = p.id) AS corrections FROM punches p JOIN employees e ON"
               " e.id = p.employee_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.q(sql + " ORDER BY p.ts", params)

    def recent(self, limit: int = 10):
        return self.db.q("SELECT p.*, e.full_name, e.code FROM punches p JOIN employees e ON"
                         " e.id = p.employee_id WHERE p.deleted = 0 ORDER BY p.created_at DESC,"
                         " p.id DESC LIMIT ?", (limit,))

    def count_on(self, day: date) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM punches WHERE deleted = 0 AND ts >= ? AND ts < ?",
                              (day.isoformat(), (day + timedelta(days=1)).isoformat()))

    # --- corrections (each requires a written reason) -----------------------
    def _need_reason(self, reason: str) -> str:
        reason = (reason or "").strip()
        if len(reason) < MIN_REASON:
            raise StoreError("سبب التعديل إلزامي: اكتب سبباً واضحاً.")
        return reason

    def _log(self, punch_id, employee_id, action, reason, old_ts=None, new_ts=None, old_kind=None,
             new_kind=None):
        u = self.db.user
        self.db.x("INSERT INTO corrections (punch_id, employee_id, action, old_ts, new_ts, old_kind,"
                  " new_kind, reason, user_id, username, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (punch_id, employee_id, action, old_ts, new_ts, old_kind, new_kind, reason,
                   u.id if u else None, u.username if u else "", self.db.now_s()))

    def add_manual(self, employee_id: int, ts: datetime, kind: str, reason: str) -> int:
        reason = self._need_reason(reason)
        if kind not in ("auto", "in", "out"):
            raise StoreError("نوع البصمة غير صحيح.")
        if ts > self.db.now() + timedelta(minutes=1):
            raise StoreError("لا يمكن إضافة بصمة بتاريخ مستقبلي.")
        with self.db.tx():
            u = self.db.user
            cur = self.db.x("INSERT INTO punches (employee_id, ts, original_ts, kind, source,"
                            " created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                            (employee_id, iso_dt(ts), iso_dt(ts), kind, "manual",
                             u.id if u else None, self.db.now_s()))
            pid = cur.lastrowid
            self._log(pid, employee_id, "add", reason, new_ts=iso_dt(ts), new_kind=kind)
            self.db.audit("إضافة بصمة يدوية", "employee", employee_id,
                          {"punch": pid, "ts": iso_dt(ts), "reason": reason})
        return pid

    def edit(self, punch_id: int, new_ts: datetime, new_kind: str, reason: str) -> None:
        reason = self._need_reason(reason)
        row = self.get(punch_id)
        if row is None:
            raise StoreError("البصمة غير موجودة.")
        if row["deleted"]:
            raise StoreError("لا يمكن تعديل بصمة محذوفة؛ استرجعها أولاً.")
        if new_ts > self.db.now() + timedelta(minutes=1):
            raise StoreError("لا يمكن نقل البصمة إلى وقت مستقبلي.")
        if iso_dt(new_ts) == row["ts"] and new_kind == row["kind"]:
            raise StoreError("لم يتغير شيء.")
        with self.db.tx():
            self.db.x("UPDATE punches SET ts = ?, kind = ? WHERE id = ?",
                      (iso_dt(new_ts), new_kind, punch_id))
            self._log(punch_id, row["employee_id"], "edit", reason, row["ts"], iso_dt(new_ts),
                      row["kind"], new_kind)
            self.db.audit("تعديل بصمة", "employee", row["employee_id"],
                          {"punch": punch_id, "from": row["ts"], "to": iso_dt(new_ts), "reason": reason})

    def delete(self, punch_id: int, reason: str) -> None:
        reason = self._need_reason(reason)
        row = self.get(punch_id)
        if row is None or row["deleted"]:
            raise StoreError("البصمة غير موجودة أو محذوفة مسبقاً.")
        with self.db.tx():
            self.db.x("UPDATE punches SET deleted = 1 WHERE id = ?", (punch_id,))
            self._log(punch_id, row["employee_id"], "delete", reason, row["ts"], None, row["kind"])
            self.db.audit("حذف بصمة", "employee", row["employee_id"],
                          {"punch": punch_id, "ts": row["ts"], "reason": reason})

    def restore(self, punch_id: int, reason: str) -> None:
        reason = self._need_reason(reason)
        row = self.get(punch_id)
        if row is None or not row["deleted"]:
            raise StoreError("البصمة ليست محذوفة.")
        with self.db.tx():
            self.db.x("UPDATE punches SET deleted = 0 WHERE id = ?", (punch_id,))
            self._log(punch_id, row["employee_id"], "restore", reason, None, row["ts"], None, row["kind"])
            self.db.audit("استرجاع بصمة", "employee", row["employee_id"],
                          {"punch": punch_id, "reason": reason})

    def corrections(self, employee_id: int | None = None, start=None, end=None,
                    punch_id: int | None = None):
        where, params = [], []
        if employee_id:
            where.append("c.employee_id = ?")
            params.append(employee_id)
        if punch_id:
            where.append("c.punch_id = ?")
            params.append(punch_id)
        if start:
            where.append("c.created_at >= ?")
            params.append(_d(start))
        if end:
            where.append("c.created_at < ?")
            params.append((parse_date(_d(end)) + timedelta(days=1)).isoformat())
        sql = ("SELECT c.*, e.full_name, e.code FROM corrections c JOIN employees e ON"
               " e.id = c.employee_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.q(sql + " ORDER BY c.created_at, c.id", params)


# ---------------------------------------------------------------------------
EVENT_LABELS = {
    "login_failed": "دخول فاشل",
    "spoof": "اشتباه انتحال (صورة/شاشة)",
    "unknown_face": "وجه غير معروف",
    "ambiguous": "تشابه بين موظفين",
    "clock_back": "إرجاع ساعة الجهاز",
    "clock_jump": "قفزة في ساعة الجهاز",
    "camera": "مشكلة في الكاميرا",
    "backup": "النسخ الاحتياطي",
    "restore": "استرجاع نسخة",
}


class EventsRepo(_Repo):
    def add(self, kind: str, details: str = "", employee_id: int | None = None,
            snapshot: str | None = None) -> int:
        cur = self.db.x("INSERT INTO security_events (ts, kind, details, employee_id, snapshot)"
                        " VALUES (?,?,?,?,?)", (self.db.now_s(), kind, details, employee_id, snapshot))
        return cur.lastrowid

    def list(self, start=None, end=None, kind: str | None = None, limit: int = 2000):
        where, params = [], []
        if start:
            where.append("ts >= ?")
            params.append(_d(start))
        if end:
            where.append("ts < ?")
            params.append((parse_date(_d(end)) + timedelta(days=1)).isoformat())
        if kind:
            where.append("kind = ?")
            params.append(kind)
        sql = "SELECT * FROM security_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.q(sql + " ORDER BY ts DESC, id DESC LIMIT ?", params + [limit])

    def unseen(self) -> int:
        return self.db.scalar("SELECT COUNT(*) FROM security_events WHERE seen = 0 AND kind <>"
                              " 'unknown_face'")

    def mark_all_seen(self) -> None:
        self.db.x("UPDATE security_events SET seen = 1 WHERE seen = 0")


class AuditRepo(_Repo):
    def list(self, start=None, end=None, search: str = "", limit: int = 5000):
        where, params = [], []
        if start:
            where.append("ts >= ?")
            params.append(_d(start))
        if end:
            where.append("ts < ?")
            params.append((parse_date(_d(end)) + timedelta(days=1)).isoformat())
        if search:
            where.append("(action LIKE ? OR details LIKE ? OR username LIKE ?)")
            params += [f"%{search}%"] * 3
        sql = "SELECT * FROM audit_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self.db.q(sql + " ORDER BY ts DESC, id DESC LIMIT ?", params + [limit])


class TemplatesRepo(_Repo):
    def list(self):
        return self.db.q("SELECT * FROM report_templates ORDER BY name")

    def save(self, name: str, definition: dict, template_id: int | None = None) -> int:
        name = name.strip()
        if not name:
            raise StoreError("اسم التقرير مطلوب.")
        payload = json.dumps(definition, ensure_ascii=False)
        now = self.db.now_s()
        with self.db.tx():
            existing = self.db.one("SELECT id FROM report_templates WHERE name = ?", (name,))
            if template_id is None and existing:
                template_id = existing["id"]
            if template_id is None:
                cur = self.db.x("INSERT INTO report_templates (name, definition, created_by,"
                                " created_at, updated_at) VALUES (?,?,?,?,?)",
                                (name, payload, self.db.user.id if self.db.user else None, now, now))
                template_id = cur.lastrowid
            else:
                self.db.x("UPDATE report_templates SET name = ?, definition = ?, updated_at = ?"
                          " WHERE id = ?", (name, payload, now, template_id))
            self.db.audit("حفظ تقرير مخصّص", "report", template_id, name)
        return template_id

    def get(self, template_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM report_templates WHERE id = ?", (template_id,))
        return None if row is None else json.loads(row["definition"])

    def delete(self, template_id: int) -> None:
        with self.db.tx():
            self.db.x("DELETE FROM report_templates WHERE id = ?", (template_id,))
            self.db.audit("حذف تقرير مخصّص", "report", template_id)
