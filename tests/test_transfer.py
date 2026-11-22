"""Moving the employees and their face prints to another computer (Windows -> Linux)."""

import json
import zipfile
from datetime import date, datetime

import cv2
import numpy as np
import pytest

from hadir.db import transfer as T
from hadir.db.schema import ADMIN_GROUP, SEED_RULES, SEED_SCHEDULE
from hadir.db.seed import seed_defaults
from hadir.db.store import Database


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def computer(path, now, org):
    db = Database(path, clock=Clock(now))
    seed_defaults(db)
    db.settings.set("org.name", org)
    return db


def chip(seed):
    img = np.random.default_rng(seed).integers(0, 255, (112, 112, 3), dtype=np.uint8)
    return cv2.imencode(".png", img)[1].tobytes()


def add_faces(db, eid, seeds):
    rng = np.random.default_rng(seeds[0] + 100)
    for s in seeds:
        db.faces.add_sample(eid, chip(s), 0.8, "front", "enroll",
                            {"arcface-r100": rng.normal(size=512).astype(np.float32),
                             "sface-2021dec": rng.normal(size=128).astype(np.float32)})


@pytest.fixture
def windows(tmp_path):
    """The department's computer: employees with faces, a guard, someone who left, a group
    with its own schedule and someone with a personal schedule."""
    db = computer(tmp_path / "windows.db", datetime(2026, 11, 22, 10, 0), "دائرة الاختبار")
    groups = {g["name"]: g["id"] for g in db.q("SELECT id, name FROM emp_groups")}
    gate_days = [(True, "07:00", "13:00", None, None)] * 5 + [(False, None, None, None, None)] * 2
    gate = db.schedules.save(dict(name="دوام البوابة", kind="weekly", days=gate_days, **SEED_RULES))
    gid = db.groups.save("حرس البوابة", "الباب الخلفي")
    db.groups.assign_schedule(gid, gate, "2000-01-01")
    people = {}
    people["ahmad"] = db.employees.save(dict(code="1001", full_name="أحمد علي حسن", phone="07701234567",
                                             job_title="محاسب", far=True, own_start="09:00", own_end="15:00",
                                             hire_date="2024-03-01", group_id=groups[ADMIN_GROUP]))
    db.employees.set_pin(people["ahmad"], "4321")
    db.employees.set_photo(people["ahmad"], b"\xff\xd8photo-of-ahmad")
    add_faces(db, people["ahmad"], [1, 2, 3])
    guards = next(g for g in groups if g.startswith("الحرس"))
    people["guard"] = db.employees.save(dict(code="1002", full_name="سامي حارس", group_id=groups[guards],
                                             cycle_start="2026-11-02", hire_date="2025-01-01"))
    add_faces(db, people["guard"], [4, 5])
    people["left"] = db.employees.save(dict(code="1003", full_name="كريم ترك الخدمة", hire_date="2023-01-01",
                                            end_date="2025-06-30", active=False, group_id=groups[ADMIN_GROUP]))
    add_faces(db, people["left"], [6])
    people["gate"] = db.employees.save(dict(code="1004", full_name="ليث البوابة", group_id=gid))
    add_faces(db, people["gate"], [7, 8])
    people["own"] = db.employees.save(dict(code="1005", full_name="هدى بجدول خاص", group_id=groups[ADMIN_GROUP]))
    db.schedules.assign(gate, "2026-01-01", employee_id=people["own"])
    return db, people


def test_everyone_moves_with_their_faces(windows, tmp_path):
    src, people = windows
    out = T.export_transfer(src, tmp_path / "نقل")
    assert out.path.name == "نقل.hadir" and out.employees == 5 and out.samples == 8
    with zipfile.ZipFile(out.path) as z:
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["format"] == "hadir-employees" and manifest["org"] == "دائرة الاختبار"

    linux = computer(tmp_path / "linux.db", datetime(2026, 12, 1, 9, 0), "دائرة الاختبار")
    pkg = T.read_package(out.path)
    plan = T.plan_import(linux, pkg)
    assert [i.action for i in plan.items] == ["new"] * 5 and not plan.warnings
    assert plan.new_groups == ["حرس البوابة"] and plan.new_schedules == ["دوام البوابة"]
    res = T.import_transfer(linux, pkg, plan)
    assert (res.added, res.existing, res.samples, res.skipped_samples) == (5, 0, 8, 0)
    assert res.groups == ["حرس البوابة"] and res.schedules == ["دوام البوابة"]

    ahmad = linux.employees.by_code("1001")
    assert (ahmad["full_name"], ahmad["phone"], ahmad["job_title"], ahmad["far"]) == (
        "أحمد علي حسن", "07701234567", "محاسب", 1)
    assert (ahmad["own_start"], ahmad["own_end"], ahmad["group_name"]) == ("09:00", "15:00", ADMIN_GROUP)
    assert ahmad["hire_date"] == "2026-12-01"  # counted here from the day of the import
    assert linux.employees.photo(ahmad["id"]) == b"\xff\xd8photo-of-ahmad"
    assert linux.employees.check_pin("1001", "4321") is not None  # the kiosk PIN still works
    left = linux.employees.by_code("1003")
    assert (left["active"], left["hire_date"], left["end_date"]) == (0, "2023-01-01", "2025-06-30")
    guard = linux.employees.by_code("1002")
    assert guard["cycle_start"] == "2026-11-02" and guard["group_name"].startswith("الحرس")

    # the faces: the same pictures and templates, ready for the recognition models
    for key, code in (("ahmad", "1001"), ("guard", "1002"), ("gate", "1004")):
        before = src.q("SELECT s.chip, v.model_id, v.vector FROM face_samples s JOIN face_embeddings v"
                       " ON v.sample_id = s.id WHERE s.employee_id = ? ORDER BY s.id, v.model_id", (people[key],))
        after = linux.q("SELECT s.chip, v.model_id, v.vector FROM face_samples s JOIN face_embeddings v"
                        " ON v.sample_id = s.id WHERE s.employee_id = ? ORDER BY s.id, v.model_id",
                        (linux.employees.by_code(code)["id"],))
        assert [tuple(r) for r in before] == [tuple(r) for r in after]
    sids, eids, mat = linux.faces.gallery("arcface-r100", 512)
    assert len(sids) == 7 and mat.shape == (7, 512)  # the one who left is not recognised

    # the group that was missing came with its own schedule, as did the personal one
    gate = linux.employees.by_code("1004")
    sched = linux.groups.current_schedule(gate["group_id"], date(2026, 12, 1))
    assert sched["name"] == "دوام البوابة"
    assert [(d["start_time"], d["end_time"]) for d in linux.schedules.days(sched["id"])][:5] == [
        ("07:00", "13:00")] * 5
    own = linux.schedules.employee_assignments(linux.employees.by_code("1005")["id"])
    assert [(a["schedule_id"], a["start_date"]) for a in own] == [(sched["id"], "2026-12-01")]
    assert linux.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'استيراد الموظفين وبصمات وجوههم'") == 1


def test_importing_the_same_file_again_changes_nothing(windows, tmp_path):
    src, _ = windows
    out = T.export_transfer(src, tmp_path / "a.hadir")
    linux = computer(tmp_path / "linux.db", datetime(2026, 12, 1, 9, 0), "د")
    T.import_transfer(linux, T.read_package(out.path))
    plan = T.plan_import(linux, T.read_package(out.path))
    assert [i.action for i in plan.items] == ["existing"] * 5
    res = T.import_transfer(linux, T.read_package(out.path), plan)
    assert (res.added, res.existing, res.samples, res.skipped_samples) == (0, 5, 0, 8)
    assert linux.scalar("SELECT COUNT(*) FROM employees") == 5
    assert linux.scalar("SELECT COUNT(*) FROM face_samples") == 8


def test_a_number_taken_here_by_someone_else_gets_a_new_number(windows, tmp_path):
    src, _ = windows
    out = T.export_transfer(src, tmp_path / "a.hadir")
    linux = computer(tmp_path / "linux.db", datetime(2026, 12, 1, 9, 0), "د")
    mine = linux.employees.save(dict(code="1001", full_name="موظف على لينكس"))
    same = linux.employees.save(dict(code="1002", full_name="سامي  حارس"))  # the same person
    add_faces(linux, same, [4])  # one of his pictures is already here
    plan = T.plan_import(linux, T.read_package(out.path))
    first = plan.items[0]
    assert (first.action, first.code, first.new_code) == ("renamed", "1001", "1006")
    assert "موظف على لينكس" in first.note
    assert (plan.items[1].action, plan.items[1].target_id) == ("existing", same)
    res = T.import_transfer(linux, T.read_package(out.path), plan)
    assert linux.employees.get(mine)["full_name"] == "موظف على لينكس"
    assert linux.employees.by_code("1006")["full_name"] == "أحمد علي حسن"
    assert any("1006" in m for m in res.messages)
    assert linux.faces.count(same) == 2 and res.skipped_samples == 1


def test_dates_can_be_kept_and_different_rules_are_pointed_out(windows, tmp_path):
    src, _ = windows
    out = T.export_transfer(src, tmp_path / "a.hadir")
    linux = computer(tmp_path / "linux.db", datetime(2026, 12, 1, 9, 0), "د")
    linux.x("UPDATE schedules SET grace_late = 20 WHERE name = ?", (SEED_SCHEDULE,))
    pkg = T.read_package(out.path)
    plan = T.plan_import(linux, pkg)
    assert len(plan.warnings) == 1 and ADMIN_GROUP in plan.warnings[0]
    T.import_transfer(linux, pkg, plan, keep_dates=True)
    assert linux.employees.by_code("1001")["hire_date"] == "2024-03-01"


def test_files_that_cannot_be_used_are_explained(windows, tmp_path):
    src, _ = windows
    plain = tmp_path / "x.hadir"
    plain.write_bytes(b"not a zip")
    with pytest.raises(T.TransferError, match="تعذّرت قراءة الملف"):
        T.read_package(plain)
    other = tmp_path / "other.hadir"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(T.TransferError, match="ليس ملف نقل"):
        T.read_package(other)
    good = T.export_transfer(src, tmp_path / "good.hadir").path
    with zipfile.ZipFile(good) as z:
        files = {n: z.read(n) for n in z.namelist()}
    newer = dict(json.loads(files["manifest.json"]), version=T.VERSION + 1)
    for name, change in (("newer.hadir", {"manifest.json": json.dumps(newer).encode()}),
                         ("short.hadir", {"faces/1/2.png": None})):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            for n, data in {**files, **change}.items():
                if data is not None:
                    z.writestr(n, data)
    with pytest.raises(T.TransferError, match="إصدار أحدث"):
        T.read_package(tmp_path / "newer.hadir")
    with pytest.raises(T.TransferError, match="ناقص"):
        T.read_package(tmp_path / "short.hadir")


def test_a_face_moved_to_another_computer_is_recognised_there(tmp_path):
    """With the real models: faces registered on one computer, recognised on the other."""
    from hadir.face.enroll import EnrollmentSession
    from hadir.face.pipeline import FaceEngine, RecognitionSession
    from hadir.face.service import load_galleries, save_captures

    from .facedata import PEOPLE, load, webcam_frames

    engine = FaceEngine("dual")
    win = computer(tmp_path / "w.db", datetime(2026, 11, 22, 10, 0), "د")
    ids = {}
    for person in "ABC":
        ids[person] = win.employees.save(dict(full_name=f"موظف {person}"))
        sess = EnrollmentSession(engine)
        for f in PEOPLE[person][:2]:
            assert sess.add_photo(load(f)) is None
        save_captures(win, ids[person], sess.final_samples())
    out = T.export_transfer(win, tmp_path / "faces.hadir")

    linux = computer(tmp_path / "l.db", datetime(2026, 12, 1, 9, 0), "د")
    T.import_transfer(linux, T.read_package(out.path))
    engine.set_galleries(load_galleries(linux, engine))
    code = win.employees.get(ids["A"])["code"]
    target = linux.employees.by_code(code)["id"]

    class Tick:
        t = 1000.0

        def __call__(self):
            return self.t

    clock = Tick()
    sess = RecognitionSession(engine, liveness="off", clock=clock)
    decision = None
    for f in webcam_frames(load(PEOPLE["A"][3]), 8, detector=engine.detector):
        _, decision = sess.process(f)
        clock.t += 0.2
        if decision:
            break
    assert decision is not None and decision.kind == "accept" and decision.employee_id == target
