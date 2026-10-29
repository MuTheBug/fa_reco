"""SQLite schema and forward-only migrations.

A migration is an SQL script, or a list of SQL scripts and Python steps; a
Python step is called as ``step(conn, now)`` inside the migration's transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SEED_SCHEDULE = "الدوام الرسمي"
# The department's rules: Sunday-Thursday from 08:00, on time until 08:30, absent after 09:00,
# exit from 14:45 (leaving earlier or not punching counts as an absence), exit punches until 17:00.
SEED_RULES = dict(grace_late=30, absent_after=60, exit_until="17:00", early_absent=1, missing_absent=1)
SEED_START, SEED_END = "08:00", "14:45"


def upgrade_seed_schedule(conn, now: datetime) -> None:
    """Give the first-run schedule of version 1.0, if nobody changed it, the department's rules.

    Days before the upgrade keep the rules they were recorded under: the old schedule is kept
    (renamed, inactive) for them and the updated one takes over from the upgrade day.
    """
    row = conn.execute("SELECT * FROM schedules WHERE name = ?", (SEED_SCHEDULE,)).fetchone()
    if row is None:
        return
    rules = tuple(row[k] for k in ("kind", "grace_late", "grace_early", "late_after_grace",
                                   "window_before", "window_after", "overtime_min", "notes", "active"))
    if rules != ("weekly", 10, 0, 0, 240, 480, 30, "", 1):
        return
    days = {r["day_index"]: (r["workday"], r["start_time"], r["end_time"])
            for r in conn.execute("SELECT * FROM schedule_days WHERE schedule_id = ?", (row["id"],))}
    seed = {i: (1, "08:00", "14:00") if i not in (4, 5) else (0, None, None) for i in range(7)}
    if days != seed:
        return
    old = row["id"]
    today = now.date()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    history = (conn.execute("SELECT COUNT(*) FROM schedule_assignments WHERE schedule_id = ? AND"
                            " start_date < ?", (old, today.isoformat())).fetchone()[0]
               and conn.execute("SELECT COUNT(*) FROM punches WHERE ts < ?",
                                (today.isoformat(),)).fetchone()[0])
    if history:
        until = (today - timedelta(days=1)).strftime("%Y/%m/%d")
        conn.execute("UPDATE schedules SET name = ?, active = 0 WHERE id = ?",
                     (f"{SEED_SCHEDULE} (حتى {until})", old))
        new = conn.execute("INSERT INTO schedules (name, kind, created_at) VALUES (?, 'weekly', ?)",
                           (SEED_SCHEDULE, stamp)).lastrowid
        for a in conn.execute("SELECT * FROM schedule_assignments WHERE schedule_id = ? AND start_date"
                              " <= ? AND (end_date IS NULL OR end_date >= ?)",
                              (old, today.isoformat(), today.isoformat())).fetchall():
            conn.execute("INSERT INTO schedule_assignments (schedule_id, group_id, employee_id, start_date,"
                         " end_date, note, created_at) VALUES (?,?,?,?,?,?,?)",
                         (new, a["group_id"], a["employee_id"], today.isoformat(), a["end_date"],
                          "قواعد الدوام الجديدة", stamp))
        conn.execute("UPDATE schedule_assignments SET schedule_id = ? WHERE schedule_id = ? AND start_date > ?",
                     (new, old, today.isoformat()))
    else:
        new = old
        conn.execute("DELETE FROM schedule_days WHERE schedule_id = ?", (new,))
    conn.execute(f"UPDATE schedules SET {', '.join(k + ' = ?' for k in SEED_RULES)} WHERE id = ?",
                 (*SEED_RULES.values(), new))
    for i in range(7):
        work = i not in (4, 5)
        conn.execute("INSERT INTO schedule_days (schedule_id, day_index, workday, start_time, end_time)"
                     " VALUES (?,?,?,?,?)", (new, i, int(work), SEED_START if work else None,
                                             SEED_END if work else None))
    conn.execute("INSERT INTO audit_log (ts, username, action, entity, entity_id, details)"
                 " VALUES (?, 'النظام', 'تطبيق قواعد الدوام الجديدة', 'schedule', ?, ?)",
                 (stamp, new, f"اعتباراً من {today.isoformat()}"))


MIGRATIONS: list = [
    # 1 -- initial schema
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        full_name TEXT NOT NULL DEFAULT '',
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'supervisor', 'viewer')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_login TEXT
    );

    CREATE TABLE emp_groups (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE schedules (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL DEFAULT 'weekly' CHECK (kind IN ('weekly', 'cycle')),
        cycle_anchor TEXT,
        grace_late INTEGER NOT NULL DEFAULT 10,
        grace_early INTEGER NOT NULL DEFAULT 0,
        late_after_grace INTEGER NOT NULL DEFAULT 0,
        window_before INTEGER NOT NULL DEFAULT 240,
        window_after INTEGER NOT NULL DEFAULT 480,
        overtime_min INTEGER NOT NULL DEFAULT 30,
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE schedule_days (
        schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
        day_index INTEGER NOT NULL,
        workday INTEGER NOT NULL,
        start_time TEXT,
        end_time TEXT,
        PRIMARY KEY (schedule_id, day_index)
    );

    CREATE TABLE employees (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        job_title TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        hire_date TEXT,
        end_date TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        photo BLOB,
        pin_hash TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE employee_groups (
        id INTEGER PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL REFERENCES emp_groups(id),
        start_date TEXT NOT NULL,
        end_date TEXT
    );
    CREATE INDEX idx_employee_groups_emp ON employee_groups(employee_id);

    CREATE TABLE schedule_assignments (
        id INTEGER PRIMARY KEY,
        schedule_id INTEGER NOT NULL REFERENCES schedules(id),
        group_id INTEGER REFERENCES emp_groups(id) ON DELETE CASCADE,
        employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE,
        start_date TEXT NOT NULL,
        end_date TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        CHECK ((group_id IS NULL) <> (employee_id IS NULL))
    );

    CREATE TABLE face_samples (
        id INTEGER PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        chip BLOB NOT NULL,
        quality REAL NOT NULL DEFAULT 0,
        pose TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'enroll',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_face_samples_emp ON face_samples(employee_id);

    CREATE TABLE face_embeddings (
        sample_id INTEGER NOT NULL REFERENCES face_samples(id) ON DELETE CASCADE,
        model_id TEXT NOT NULL,
        vector BLOB NOT NULL,
        PRIMARY KEY (sample_id, model_id)
    );

    CREATE TABLE leave_types (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        short TEXT NOT NULL DEFAULT '',
        unit TEXT NOT NULL DEFAULT 'day' CHECK (unit IN ('day', 'hour')),
        paid INTEGER NOT NULL DEFAULT 1,
        counts_present INTEGER NOT NULL DEFAULT 0,
        calendar_days INTEGER NOT NULL DEFAULT 0,
        quota REAL,
        quota_period TEXT NOT NULL DEFAULT 'year' CHECK (quota_period IN ('year', 'month')),
        color TEXT NOT NULL DEFAULT '#3b82f6',
        active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE leaves (
        id INTEGER PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        leave_type_id INTEGER NOT NULL REFERENCES leave_types(id),
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        start_time TEXT,
        end_time TEXT,
        reference TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        cancelled INTEGER NOT NULL DEFAULT 0,
        cancel_reason TEXT,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_leaves_emp ON leaves(employee_id, start_date);

    CREATE TABLE holidays (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE holiday_groups (
        holiday_id INTEGER NOT NULL REFERENCES holidays(id) ON DELETE CASCADE,
        group_id INTEGER NOT NULL REFERENCES emp_groups(id) ON DELETE CASCADE,
        PRIMARY KEY (holiday_id, group_id)
    );

    CREATE TABLE punches (
        id INTEGER PRIMARY KEY,
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        ts TEXT NOT NULL,
        original_ts TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'auto' CHECK (kind IN ('auto', 'in', 'out')),
        source TEXT NOT NULL DEFAULT 'face' CHECK (source IN ('face', 'manual', 'pin', 'import')),
        score REAL,
        liveness REAL,
        snapshot TEXT,
        deleted INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER REFERENCES users(id),
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_punches_emp_ts ON punches(employee_id, ts);
    CREATE INDEX idx_punches_ts ON punches(ts);

    -- Every change to the attendance record is kept here with its written reason.
    CREATE TABLE corrections (
        id INTEGER PRIMARY KEY,
        punch_id INTEGER NOT NULL REFERENCES punches(id),
        employee_id INTEGER NOT NULL REFERENCES employees(id),
        action TEXT NOT NULL CHECK (action IN ('add', 'edit', 'delete', 'restore')),
        old_ts TEXT,
        new_ts TEXT,
        old_kind TEXT,
        new_kind TEXT,
        reason TEXT NOT NULL CHECK (length(trim(reason)) >= 3),
        user_id INTEGER REFERENCES users(id),
        username TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_corrections_emp ON corrections(employee_id, created_at);
    CREATE INDEX idx_corrections_punch ON corrections(punch_id);

    -- Corrections can never be altered or removed.
    CREATE TRIGGER corrections_no_update BEFORE UPDATE ON corrections
    BEGIN SELECT RAISE(ABORT, 'corrections are append-only'); END;
    CREATE TRIGGER corrections_no_delete BEFORE DELETE ON corrections
    BEGIN SELECT RAISE(ABORT, 'corrections are append-only'); END;
    -- Punches are never physically deleted (soft delete with a reason instead).
    CREATE TRIGGER punches_no_delete BEFORE DELETE ON punches
    BEGIN SELECT RAISE(ABORT, 'punches cannot be deleted'); END;

    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        user_id INTEGER,
        username TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        entity TEXT NOT NULL DEFAULT '',
        entity_id INTEGER,
        details TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_audit_ts ON audit_log(ts);

    CREATE TABLE security_events (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        details TEXT NOT NULL DEFAULT '',
        employee_id INTEGER,
        snapshot TEXT,
        seen INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_security_ts ON security_events(ts);

    CREATE TABLE report_templates (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        definition TEXT NOT NULL,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    # 2 -- strict attendance rules, Telegram outbox, automatic hire dates
    [
        """
        ALTER TABLE schedules ADD COLUMN absent_after INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE schedules ADD COLUMN exit_until TEXT;
        ALTER TABLE schedules ADD COLUMN early_absent INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE schedules ADD COLUMN missing_absent INTEGER NOT NULL DEFAULT 0;

        -- Messages for Telegram wait here until they are sent, so none is lost while offline.
        CREATE TABLE outbox (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            sent_at TEXT,
            dropped INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_outbox_pending ON outbox(sent_at, dropped);

        -- The hire date is no longer typed in: people count from the day they were added (or
        -- from their first punch or leave, if earlier).
        UPDATE employees SET hire_date = MIN(
            substr(created_at, 1, 10),
            COALESCE((SELECT substr(MIN(p.ts), 1, 10) FROM punches p WHERE p.employee_id = employees.id),
                     '9999-12-31'),
            COALESCE((SELECT MIN(l.start_date) FROM leaves l WHERE l.employee_id = employees.id),
                     '9999-12-31'))
        WHERE hire_date IS NULL;
        """,
        upgrade_seed_schedule,
    ],
]
