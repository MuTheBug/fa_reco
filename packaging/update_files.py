#!/usr/bin/env python3
"""Update files for Hadir: only what changed since the earlier releases.

The full installer carries the whole program: about 300 files, 600 MB before
compression. From one version to the next only a few of them change (the
program itself, Hadir.exe, and a couple of build files); the face models and
the Qt, OpenCV and ONNX Runtime libraries stay the same byte for byte.
packaging/update.iss builds the update file HadirUpdate-<version>.exe from
what this script prepares: the changed files, plus the SHA-256 of every file
it leaves in place, which the update checks on the computer before it copies
anything.

  update_files.py manifest DIST VERSION
      writes DIST/hadir-files.txt: SHA-256, size and path of every file
  update_files.py bases OWNER/REPO VERSION OUT [--known DIR]
      the file lists of the releases before VERSION, as OUT/<version>.txt:
      their Hadir-files-<version>.txt, else DIR/<version>.txt (the lists of the
      releases made before there were any, in packaging/manifests), else for the
      latest one the list made from its portable zip
  update_files.py plan DIST BASES OUT
      OUT/payload (the files the update copies), OUT/check.txt, OUT/delete.iss
      and OUT/info.iss, read by update.iss
  update_files.py verify FOLDER MANIFEST
      checks an installed program against a file list
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "hadir-files.txt"
# written next to Hadir.exe, but not part of the program
NOT_PROGRAM = {MANIFEST, "portable.txt"}
HEADER = "# hadir-files 1"
# older releases join an update only while it stays this small
OLDER_BASES_LIMIT = 60_000_000
LINE = re.compile(r"([0-9a-f]{64}) (\d+) (\S.*)")
TAG = re.compile(r"v(\d+\.\d+\.\d+)")

Files = dict  # posix path -> (sha256, size)


def version_key(version: str) -> tuple:
    return tuple(int(x) for x in version.split("."))


def file_hash(path: Path) -> tuple:
    h, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def is_program_file(rel: str) -> bool:
    # the portable copy keeps its data in a "data" folder beside Hadir.exe
    return rel not in NOT_PROGRAM and rel.split("/", 1)[0] != "data"


def safe_path(rel: str) -> bool:
    parts = rel.split("/")
    return bool(rel) and "\\" not in rel and ":" not in rel and not rel.startswith("/") \
        and all(p not in ("", ".", "..") for p in parts)


def scan(root: Path) -> Files:
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and is_program_file(rel):
            files[rel] = file_hash(path)
    return files


def write_manifest(path: Path, version: str, files: Files) -> None:
    lines = [HEADER, f"# version {version}"]
    lines += [f"{sha} {size} {rel}" for rel, (sha, size) in sorted(files.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def read_manifest(path: Path) -> tuple:
    """(version, files) of a file list written by write_manifest()."""
    version, files = "", {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("# version "):
            version = line[len("# version "):].strip()
        if not line or line.startswith("#"):
            continue
        m = LINE.fullmatch(line)
        if not m or not safe_path(m[3]):
            raise ValueError(f"{path}:{n}: not a file list line: {line!r}")
        files[m[3]] = (m[1], int(m[2]))
    if not files:
        raise ValueError(f"{path}: the file list is empty")
    return version, files


def zip_files(path: Path) -> Files:
    """The program files in a portable zip (one folder, Hadir/, with the program)."""
    files = {}
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        names = [i.filename.replace("\\", "/") for i in infos]
        tops = {n.split("/", 1)[0] for n in names}
        prefix = f"{tops.pop()}/" if len(tops) == 1 and all("/" in n for n in names) else ""
        for info, name in zip(infos, names):
            rel = name[len(prefix):]
            if not is_program_file(rel):
                continue
            h, size = hashlib.sha256(), 0
            with z.open(info) as f:  # checks the CRC: a damaged zip fails here
                while chunk := f.read(1 << 20):
                    h.update(chunk)
                    size += len(chunk)
            files[rel] = (h.hexdigest(), size)
    return files


@dataclass
class Plan:
    copy: list    # files the update carries: new, or different in an earlier version
    check: list   # files it leaves in place; they must already be on the computer
    delete: list  # files of earlier versions that this one no longer has
    bases: list   # the versions it was made against, newest first


def make_plan(target: Files, bases: list) -> Plan:
    if not bases:
        raise ValueError("an update needs at least one earlier version")
    copy = sorted(p for p in target if any(files.get(p) != target[p] for _, files in bases))
    carried = set(copy)
    check = sorted(p for p in target if p not in carried)
    lower = {p.lower() for p in target}  # Windows file names ignore case
    delete = sorted({p for _, files in bases for p in files if p.lower() not in lower})
    return Plan(copy, check, delete, [v for v, _ in bases])


def payload_size(target: Files, plan: Plan) -> int:
    return sum(target[p][1] for p in plan.copy)


def choose_bases(target: Files, found: list, limit: int = OLDER_BASES_LIMIT) -> list:
    """The latest earlier version, then older ones while the update stays small."""
    found = sorted(found, key=lambda b: version_key(b[0]), reverse=True)
    chosen = found[:1]
    for base in found[1:]:
        if payload_size(target, make_plan(target, chosen + [base])) > limit:
            break
        chosen.append(base)
    return chosen


def from_text(versions: list) -> str:
    """Arabic: "الإصدار 1.3.0" or "الإصدارات 1.3.1 و1.3.0"."""
    if len(versions) == 1:
        return f"الإصدار {versions[0]}"
    return "الإصدارات " + "، ".join(versions[:-1]) + " و" + versions[-1]


def windows_path(rel: str) -> str:
    if not rel.isascii():
        raise ValueError(f"file names must be ASCII for the update checks: {rel!r}")
    return rel.replace("/", "\\")


def write_update(dist: Path, target: Files, plan: Plan, out: Path) -> None:
    # one line per file left in place: "<sha256> <path>", read by update.iss
    check = "".join(f"{target[rel][0]} {windows_path(rel)}\r\n" for rel in plan.check)
    if out.exists():
        shutil.rmtree(out)
    payload = out / "payload"
    for rel in plan.copy + [MANIFEST]:
        dst = payload / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dist / rel, dst)
    (out / "check.txt").write_text(check, encoding="ascii", newline="")
    lines = ["[InstallDelete]"]
    lines += ['Type: files; Name: "{app}\\%s"' % windows_path(rel).replace("{", "{{") for rel in plan.delete]
    (out / "delete.iss").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    info = {
        "UpdateFrom": ", ".join(plan.bases),
        "UpdateFromText": from_text(plan.bases),
        "UpdateFiles": str(len(plan.copy)),
        "UpdateChecked": str(len(plan.check)),
    }
    text = "".join(f'#define {k} "{v}"\n' for k, v in info.items())
    (out / "info.iss").write_text(text, encoding="utf-8-sig")


def verify(folder: Path, files: Files) -> list:
    problems = []
    for rel, (sha, size) in sorted(files.items()):
        path = folder / rel
        if not path.is_file():
            problems.append(f"missing: {rel}")
        elif path.stat().st_size != size or file_hash(path)[0] != sha:
            problems.append(f"different: {rel}")
    return problems


# --- earlier releases on GitHub -------------------------------------------------------------------

def _open(url: str, token: str, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "hadir-build"})
    if token:  # not sent on to the storage host that serves the download
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=300)


def _retry(action, what: str, tries: int = 4):
    for attempt in range(tries):
        try:
            return action()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries - 1:
                raise
            error = e
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
            if attempt == tries - 1:
                raise
            error = e
        print(f"{what}: {error}; retrying", flush=True)
        time.sleep(2 ** (attempt + 1))


def _download(asset: dict, dest: Path, token: str) -> None:
    url = asset["url"] if token else asset["browser_download_url"]

    def get():
        with _open(url, token, "application/octet-stream") as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if dest.stat().st_size != asset["size"]:
            raise ConnectionError(f"incomplete download ({dest.stat().st_size} of {asset['size']} bytes)")

    _retry(get, asset["name"])


def fetch_bases(repo: str, version: str, out: Path, token: str = "", known: Path = None) -> list:
    """Save the file lists of the releases before `version`; returns their versions."""
    def listing():
        with _open(f"https://api.github.com/repos/{repo}/releases?per_page=100", token) as r:
            return json.load(r)

    current = version_key(version)
    earlier = []
    for rel in _retry(listing, "releases"):
        m = TAG.fullmatch(rel.get("tag_name") or "")
        if rel.get("draft") or rel.get("prerelease") or not m or version_key(m[1]) >= current:
            continue
        earlier.append((version_key(m[1]), m[1], {a["name"]: a for a in rel.get("assets", [])}))
    earlier.sort(reverse=True)
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, (_, v, assets) in enumerate(earlier):
        dest = out / f"{v}.txt"
        listed, portable = assets.get(f"Hadir-files-{v}.txt"), assets.get(f"Hadir-Portable-{v}.zip")
        kept = known / f"{v}.txt" if known else None
        if listed:
            _download(listed, dest, token)
            read_manifest(dest)
            print(f"{v}: file list of the release")
        elif kept and kept.is_file():
            read_manifest(kept)
            shutil.copyfile(kept, dest)
            print(f"{v}: file list from {kept}")
        elif i == 0 and portable:
            with tempfile.TemporaryDirectory() as tmp:
                z = Path(tmp) / portable["name"]
                _download(portable, z, token)
                write_manifest(dest, v, zip_files(z))
            print(f"{v}: file list made from {portable['name']}")
        else:
            print(f"{v}: no file list, skipped")
            continue
        saved.append(v)
    return saved


# --- command line ---------------------------------------------------------------------------------

def _github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("manifest")
    p.add_argument("dist", type=Path)
    p.add_argument("version")
    p = sub.add_parser("bases")
    p.add_argument("repo")
    p.add_argument("version")
    p.add_argument("out", type=Path)
    p.add_argument("--known", type=Path, default=Path(__file__).resolve().parent / "manifests")
    p = sub.add_parser("plan")
    p.add_argument("dist", type=Path)
    p.add_argument("bases", type=Path)
    p.add_argument("out", type=Path)
    p = sub.add_parser("verify")
    p.add_argument("folder", type=Path)
    p.add_argument("manifest", type=Path)
    args = ap.parse_args(argv)

    if args.command == "manifest":
        files = scan(args.dist)
        write_manifest(args.dist / MANIFEST, args.version, files)
        print(f"{len(files)} files, {sum(s for _, s in files.values()) / 1e6:.1f} MB")
    elif args.command == "bases":
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        found = fetch_bases(args.repo, args.version, args.out, token, args.known)
        print("earlier releases: " + (", ".join(found) or "none"))
    elif args.command == "plan":
        _, target = read_manifest(args.dist / MANIFEST)
        found = []
        for f in sorted(args.bases.glob("*.txt")):
            version, files = read_manifest(f)
            found.append((version or f.stem, files))
        if not found:
            print("no earlier release: no update file")
            _github_output(**{"from": ""})
            return 0
        plan = make_plan(target, choose_bases(target, found))
        write_update(args.dist, target, plan, args.out)
        print(f"update from {', '.join(plan.bases)}: copies {len(plan.copy)} files"
              f" ({payload_size(target, plan) / 1e6:.1f} MB), checks {len(plan.check)}, deletes {len(plan.delete)}")
        for rel in plan.copy:
            print(f"  copy   {rel}")
        for rel in plan.delete:
            print(f"  delete {rel}")
        _github_output(**{"from": ", ".join(plan.bases), "from_text": from_text(plan.bases)})
    elif args.command == "verify":
        version, files = read_manifest(args.manifest)
        problems = verify(args.folder, files)
        for line in problems:
            print(line)
        print(f"{args.folder}: {len(files) - len(problems)} of {len(files)} files of {version} as built")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
