"""Moving the employees and their face prints to another computer running Hadir (for example
from the Windows laptop to a Linux one), so that nobody has to register their face again.

The transfer file (``.hadir``) is a zip archive:

* ``manifest.json``  what it is, from which program version and department, and how many;
* ``employees.json`` every employee: details, group, personal schedule, kiosk PIN (as stored,
  never readable), photo and face samples;
* ``schedules.json`` the working-time schedules those groups and people follow, so that a
  group missing on the other computer is created with its own rules;
* ``photos/<n>.jpg``, ``faces/<n>/<k>.png`` (the aligned face pictures the recognition models
  read) and ``faces/<n>/<k>.<model>.f32`` (their templates for each model).

Face prints are sensitive: the file should be carried safely and deleted after the import.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .store import Database, StoreError

FORMAT = "hadir-employees"
VERSION = 1
SUFFIX = ".hadir"
# The recognition models' templates (float32): ArcFace R100 and SFace. Others are left out and,
# when needed, computed again from the face pictures.
EMBEDDING_DIMS = {"arcface-r100": 512, "sface-2021dec": 128}
EMPLOYEE_FIELDS = ("code", "full_name", "job_title", "department", "phone", "notes", "hire_date",
                   "end_date", "active", "cycle_start", "own_start", "own_end", "far")
# Not compared when telling whether two computers' schedules follow the same rules.
SCHEDULE_IGNORED = ("id", "name", "created_at", "cycle_anchor", "active", "notes")


class TransferError(Exception):
    """A transfer file that cannot be used (Arabic message)."""


def _same_name(a: str, b: str) -> bool:
    return " ".join((a or "").split()) == " ".join((b or "").split())


def _schedule_definition(db: Database, schedule_id: int) -> dict:
    row = db.one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
    days = db.q("SELECT * FROM schedule_days WHERE schedule_id = ? ORDER BY day_index", (schedule_id,))
    return {"row": {k: row[k] for k in row.keys() if k not in ("id", "created_at")},
            "days": [{k: d[k] for k in d.keys() if k != "schedule_id"} for d in days]}


def _signature(definition: dict):
    row = tuple(sorted((k, v) for k, v in definition["row"].items() if k not in SCHEDULE_IGNORED))
    days = tuple(sorted(tuple(sorted(d.items())) for d in definition["days"]))
    return row, days


def _personal_schedule(db: Database, employee_id: int, on: date):
    return db.one("SELECT s.* FROM schedule_assignments a JOIN schedules s ON s.id = a.schedule_id"
                  " WHERE a.employee_id = ? AND a.start_date <= ? AND (a.end_date IS NULL OR a.end_date >= ?)"
                  " ORDER BY a.start_date DESC, a.id DESC LIMIT 1", (employee_id, on.isoformat(), on.isoformat()))


# ---------------------------------------------------------------- export
@dataclass
class ExportResult:
    path: Path
    employees: int
    samples: int


def export_transfer(db: Database, path: str | Path, include_inactive: bool = True) -> ExportResult:
    """Write every employee with their face samples to a transfer file."""
    from .. import __version__

    path = Path(path)
    if path.suffix.lower() != SUFFIX:
        path = path.with_name(path.name + SUFFIX)
    today = db.now().date()
    people = db.q("SELECT * FROM employees" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY id")
    schedules: dict[str, dict] = {}
    items, samples = [], 0
    part = path.with_name(path.name + ".part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for n, e in enumerate(people, start=1):
                item = {f: e[f] for f in EMPLOYEE_FIELDS}
                item["pin_hash"] = e["pin_hash"]
                group = db.one("SELECT g.* FROM employee_groups eg JOIN emp_groups g ON g.id = eg.group_id"
                               " WHERE eg.employee_id = ? AND eg.end_date IS NULL", (e["id"],))
                if group is not None:
                    item["group"] = group["name"]
                    item["group_description"] = group["description"]
                    sched = db.groups.current_schedule(group["id"], today)
                    if sched is not None:
                        item["group_schedule"] = sched["name"]
                        schedules.setdefault(sched["name"], _schedule_definition(db, sched["id"]))
                own = _personal_schedule(db, e["id"], today)
                if own is not None:
                    item["personal_schedule"] = own["name"]
                    schedules.setdefault(own["name"], _schedule_definition(db, own["id"]))
                if e["photo"]:
                    item["photo"] = f"photos/{n}.jpg"
                    z.writestr(item["photo"], e["photo"])
                faces = []
                rows = db.q("SELECT * FROM face_samples WHERE employee_id = ? ORDER BY id", (e["id"],))
                for k, s in enumerate(rows, start=1):
                    base = f"faces/{n}/{k}"
                    z.writestr(base + ".png", s["chip"])
                    vectors = {}
                    for v in db.q("SELECT model_id, vector FROM face_embeddings WHERE sample_id = ?"
                                  " ORDER BY model_id", (s["id"],)):
                        name = f"{base}.{v['model_id']}.f32"
                        z.writestr(name, v["vector"])
                        vectors[v["model_id"]] = name
                    faces.append({"chip": base + ".png", "quality": s["quality"], "pose": s["pose"],
                                  "source": s["source"], "created_at": s["created_at"], "embeddings": vectors})
                item["faces"] = faces
                samples += len(faces)
                items.append(item)
            manifest = {"format": FORMAT, "version": VERSION, "app_version": __version__,
                        "created_at": db.now_s(), "org": db.settings.get("org.name") or "",
                        "employees": len(items), "samples": samples}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            z.writestr("employees.json", json.dumps(items, ensure_ascii=False, indent=1))
            z.writestr("schedules.json", json.dumps(schedules, ensure_ascii=False, indent=1))
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)
    db.audit("تصدير الموظفين وبصمات وجوههم", "employee", None,
             {"file": str(path), "employees": len(items), "samples": samples})
    return ExportResult(path, len(items), samples)


# ---------------------------------------------------------------- reading
@dataclass
class Package:
    path: Path
    manifest: dict
    employees: list
    schedules: dict

    @property
    def samples(self) -> int:
        return sum(len(e["faces"]) for e in self.employees)


def read_package(path: str | Path) -> Package:
    """Open and check a transfer file (TransferError with the reason if it cannot be used)."""
    path = Path(path)
    unreadable = "تعذّرت قراءة الملف: قد يكون تالفاً أو لم يكتمل نسخه. انسخه من جديد."
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "manifest.json" not in names or "employees.json" not in names:
                raise TransferError("هذا ليس ملف نقل موظفين من برنامج حاضر.")
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                raise TransferError("هذا ليس ملف نقل موظفين من برنامج حاضر.")
            if int(manifest.get("version") or 0) > VERSION:
                raise TransferError("الملف من إصدار أحدث من حاضر: حدّث البرنامج على هذا الجهاز أولاً.")
            employees = json.loads(z.read("employees.json").decode("utf-8"))
            schedules = (json.loads(z.read("schedules.json").decode("utf-8"))
                         if "schedules.json" in names else {})
            bad = z.testzip()
    except TransferError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError, TypeError):
        raise TransferError(unreadable) from None
    if bad is not None or not isinstance(employees, list) or not isinstance(schedules, dict):
        raise TransferError(unreadable)
    for e in employees:
        if (not isinstance(e, dict) or not str(e.get("full_name") or "").strip()
                or not isinstance(e.get("faces", []), list)):
            raise TransferError(unreadable)
        e.setdefault("faces", [])
        wanted = [e["photo"]] if e.get("photo") else []
        for f in e["faces"]:
            if not isinstance(f, dict) or not isinstance(f.get("embeddings", {}), dict):
                raise TransferError(unreadable)
            wanted += [f.get("chip"), *f.get("embeddings", {}).values()]
        if any(w not in names for w in wanted):
            raise TransferError("الملف ناقص (بعض صور الوجوه غير موجودة فيه). صدّره من جديد.")
    return Package(path, manifest, employees, schedules)


# ---------------------------------------------------------------- planning
@dataclass
class PlanItem:
    index: int  # in Package.employees
    name: str
    code: str  # in the file
    new_code: str  # the employee number it will have here
    action: str  # new | renamed (number taken here by someone else) | existing (only the faces are added)
    target_id: int | None
    group: str
    faces: int
    active: bool
    note: str = ""


@dataclass
class ImportPlan:
    items: list
    new_groups: list = field(default_factory=list)
    new_schedules: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def count(self, action: str) -> int:
        return sum(1 for i in self.items if i.action == action)


ACTIONS = {"new": "يُضاف", "renamed": "يُضاف برقم وظيفي جديد", "existing": "موجود: تُضاف بصمات وجهه فقط"}


def plan_import(db: Database, pkg: Package) -> ImportPlan:
    """What the import will do, for the person to check before it happens."""
    by_code = {r["code"]: r for r in db.q("SELECT id, code, full_name FROM employees")}
    names = {}
    for r in by_code.values():
        names.setdefault(" ".join(r["full_name"].split()), r["code"])
    numbers = [int(c) for c in list(by_code) + [str(e.get("code") or "") for e in pkg.employees] if c.isdigit()]
    next_number = max(numbers, default=1000) + 1
    taken = set(by_code)
    groups_here = {g["name"]: g["id"] for g in db.q("SELECT id, name FROM emp_groups")}
    schedules_here = {s["name"]: s["id"] for s in db.q("SELECT id, name FROM schedules")}
    plan = ImportPlan([])
    for index, e in enumerate(pkg.employees):
        name, code = str(e["full_name"]).strip(), str(e.get("code") or "").strip()
        here = by_code.get(code) if code else None
        item = PlanItem(index, name, code, code, "new", None, str(e.get("group") or ""), len(e["faces"]),
                        bool(e.get("active", 1)))
        if here is not None and _same_name(here["full_name"], name):
            item.action, item.target_id = "existing", here["id"]
        else:
            if here is not None or not code or code in taken:
                while str(next_number) in taken:
                    next_number += 1
                item.new_code = str(next_number)
                item.action = "renamed"
                if here is not None:
                    item.note = f"الرقم {code} لـ«{here['full_name']}» هنا"
            twin = names.get(" ".join(name.split()))
            if twin is not None:
                item.note = (item.note + "؛ " if item.note else "") + f"يوجد هنا موظف بالاسم نفسه برقم {twin}"
            taken.add(item.new_code)
            if item.group and item.group not in groups_here and item.group not in plan.new_groups:
                plan.new_groups.append(item.group)
            for key in ("group_schedule", "personal_schedule"):
                s = e.get(key)
                if key == "group_schedule" and item.group in groups_here:
                    continue  # a group that is here keeps its own schedule
                if s and s not in schedules_here and s in pkg.schedules and s not in plan.new_schedules:
                    plan.new_schedules.append(s)
        plan.items.append(item)
    # groups that are on both computers but follow different rules
    today = db.now().date()
    seen = set()
    for item in plan.items:
        e = pkg.employees[item.index]
        g, s = item.group, e.get("group_schedule")
        if not g or g in seen or g not in groups_here or s not in pkg.schedules:
            continue
        seen.add(g)
        mine = db.groups.current_schedule(groups_here[g], today)
        try:
            differ = mine is None or _signature(_schedule_definition(db, mine["id"])) != _signature(pkg.schedules[s])
        except (KeyError, TypeError, AttributeError):
            differ = False
        if differ:
            plan.warnings.append(
                f"مجموعة «{g}»: قواعد دوامها على هذا الجهاز تختلف عن الجهاز الآخر. يتبع الموظفون المستوردون"
                " قواعد هذا الجهاز؛ راجعها من «الإعدادات ← قواعد الدوام» إن لزم.")
    return plan


# ---------------------------------------------------------------- import
@dataclass
class ImportResult:
    added: int = 0
    existing: int = 0
    samples: int = 0
    skipped_samples: int = 0  # already here
    groups: list = field(default_factory=list)
    schedules: list = field(default_factory=list)
    messages: list = field(default_factory=list)


def _create_schedule(db: Database, name: str, definition: dict) -> int:
    cols = [c[1] for c in db.q("PRAGMA table_info(schedules)") if c[1] not in ("id", "name", "created_at")]
    row = definition["row"]
    use = [c for c in cols if c in row]
    sid = db.x(f"INSERT INTO schedules (name, created_at{''.join(', ' + c for c in use)})"
               f" VALUES (?, ?{', ?' * len(use)})", [name, db.now_s(), *[row[c] for c in use]]).lastrowid
    day_cols = [c[1] for c in db.q("PRAGMA table_info(schedule_days)") if c[1] != "schedule_id"]
    for d in definition["days"]:
        use_d = [c for c in day_cols if c in d]
        db.x(f"INSERT INTO schedule_days (schedule_id{''.join(', ' + c for c in use_d)})"
             f" VALUES (?{', ?' * len(use_d)})", [sid, *[d[c] for c in use_d]])
    db.audit("إضافة جدول دوام", "schedule", sid, f"{name} (من ملف نقل)")
    return sid


def import_transfer(db: Database, pkg: Package, plan: ImportPlan | None = None,
                    keep_dates: bool = False) -> ImportResult:
    """Add the employees and their face samples (all or nothing).

    The people added count on this computer from today, unless ``keep_dates``: their earlier
    days were recorded on the other computer. Employees already here only gain the face
    samples they do not have yet, so importing the same file twice changes nothing.
    """
    plan = plan or plan_import(db, pkg)
    try:
        with zipfile.ZipFile(pkg.path) as z:
            return _import(db, pkg, plan, keep_dates, z)
    except StoreError as exc:
        raise TransferError(f"{exc} لم يُستورد شيء.") from None
    except (zipfile.BadZipFile, OSError, KeyError, ValueError, TypeError, AttributeError):
        raise TransferError("تعذّرت قراءة الملف: قد يكون تالفاً أو لم يكتمل نسخه. انسخه من جديد."
                            " لم يُستورد شيء.") from None


def _import(db: Database, pkg: Package, plan: ImportPlan, keep_dates: bool, z: zipfile.ZipFile) -> ImportResult:
    res = ImportResult()
    today = db.now().date()
    with db.tx():
        schedules_here = {s["name"]: s["id"] for s in db.q("SELECT id, name FROM schedules")}
        groups_here = {g["name"]: g["id"] for g in db.q("SELECT id, name FROM emp_groups")}

        def schedule_id(name: str | None) -> int | None:
            if not name:
                return None
            if name not in schedules_here and name in pkg.schedules:
                try:
                    schedules_here[name] = _create_schedule(db, name, pkg.schedules[name])
                    res.schedules.append(name)
                except (KeyError, TypeError, AttributeError):
                    return None
            return schedules_here.get(name)

        def group_id(item: PlanItem, e: dict) -> int | None:
            if not item.group:
                return db.employees.default_group()
            if item.group not in groups_here:
                gid = db.groups.save(item.group, str(e.get("group_description") or ""))
                sid = schedule_id(e.get("group_schedule"))
                if sid is None:
                    fallback = db.groups.current_schedule(db.employees.default_group(), today)
                    sid = fallback["id"] if fallback is not None else None
                    res.messages.append(f"المجموعة «{item.group}» أُضيفت بجدول المجموعة الافتراضية؛ راجعه.")
                if sid is not None:
                    db.groups.assign_schedule(gid, sid, "2000-01-01")
                groups_here[item.group] = gid
                res.groups.append(item.group)
            return groups_here[item.group]

        for item in plan.items:
            e = pkg.employees[item.index]
            if item.action == "existing":
                eid = item.target_id
                res.existing += 1
                if e.get("photo") and not db.employees.photo(eid):
                    db.employees.set_photo(eid, z.read(e["photo"]))
            else:
                data = {f: e.get(f) for f in EMPLOYEE_FIELDS if f in e}
                data["code"] = item.new_code
                data["full_name"] = item.name
                if data.get("active", 1) and not keep_dates:
                    hire = str(data.get("hire_date") or "")
                    data["hire_date"] = max(hire, today.isoformat())
                    data["end_date"] = None
                data["group_id"] = group_id(item, e)
                try:
                    eid = db.employees.save(data)
                except StoreError as exc:
                    raise TransferError(f"«{item.name}»: {exc} لم يُستورد شيء.") from None
                if e.get("pin_hash"):
                    db.x("UPDATE employees SET pin_hash = ? WHERE id = ?", (e["pin_hash"], eid))
                if e.get("photo"):
                    db.employees.set_photo(eid, z.read(e["photo"]))
                sid = schedule_id(e.get("personal_schedule"))
                if sid is not None:
                    start = db.scalar("SELECT hire_date FROM employees WHERE id = ?", (eid,)) or today.isoformat()
                    db.schedules.assign(sid, start, employee_id=eid, note="من ملف نقل")
                res.added += 1
                if item.action == "renamed":
                    res.messages.append(f"«{item.name}»: الرقم الوظيفي هنا {item.new_code}"
                                        f"{' بدل ' + item.code if item.code else ''}.")
            have = {hashlib.sha256(r["chip"]).digest()
                    for r in db.q("SELECT chip FROM face_samples WHERE employee_id = ?", (eid,))}
            for f in e["faces"]:
                chip = z.read(f["chip"])
                key = hashlib.sha256(chip).digest()
                if key in have:
                    res.skipped_samples += 1
                    continue
                have.add(key)
                sample = db.x("INSERT INTO face_samples (employee_id, chip, quality, pose, source, created_at)"
                              " VALUES (?,?,?,?,?,?)",
                              (eid, chip, float(f.get("quality") or 0), str(f.get("pose") or ""),
                               str(f.get("source") or "enroll"), str(f.get("created_at") or db.now_s()))).lastrowid
                for model_id, name in f.get("embeddings", {}).items():
                    vector = z.read(name)
                    if EMBEDDING_DIMS.get(model_id, -1) * 4 == len(vector):
                        db.x("INSERT OR REPLACE INTO face_embeddings (sample_id, model_id, vector) VALUES (?,?,?)",
                             (sample, model_id, vector))
                res.samples += 1
        db.audit("استيراد الموظفين وبصمات وجوههم", "employee", None,
                 {"file": str(pkg.path), "added": res.added, "existing": res.existing, "samples": res.samples,
                  "from": pkg.manifest.get("org", "")})
    return res
