"""The update file: which files it carries, which it checks in place, which it removes."""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import update_files as uf  # noqa: E402


def make_dist(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


PROGRAM = {
    "Hadir.exe": b"exe 1",
    "_internal/base_library.zip": b"stdlib",
    "_internal/models/arcface.onnx": b"model" * 100,
    "_internal/PySide6/Qt6Core.dll": b"qt",
    "_internal/old.pyd": b"old",
}


def entries(files: dict) -> dict:
    return {rel: (hashlib.sha256(data).hexdigest(), len(data)) for rel, data in files.items()}


def test_manifest_lists_the_program_files_only(tmp_path):
    dist = make_dist(tmp_path / "Hadir", {**PROGRAM, "portable.txt": b"p", "data/hadir.db": b"db",
                                          "hadir-files.txt": b"old list"})
    files = uf.scan(dist)
    assert set(files) == set(PROGRAM)
    uf.write_manifest(dist / uf.MANIFEST, "1.3.1", files)
    version, again = uf.read_manifest(dist / uf.MANIFEST)
    assert (version, again) == ("1.3.1", files) and again == entries(PROGRAM)
    assert (dist / uf.MANIFEST).read_text().startswith("# hadir-files 1\n# version 1.3.1\n")


@pytest.mark.parametrize("line", ["0" * 64 + " 1 ../outside.dll", "0" * 64 + " 1 /abs.dll",
                                  "0" * 64 + " 1 a\\b.dll", "0" * 63 + " 1 short.dll", "x" * 64 + " 1 a.dll",
                                  "0" * 64 + " one a.dll", "0" * 64 + " 1 C:/x.dll"])
def test_bad_file_lists_are_refused(tmp_path, line):
    p = tmp_path / "list.txt"
    p.write_text(f"# hadir-files 1\n{line}\n")
    with pytest.raises(ValueError):
        uf.read_manifest(p)


def test_empty_file_list_is_refused(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("# hadir-files 1\n# version 1.0.0\n")
    with pytest.raises(ValueError):
        uf.read_manifest(p)


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_file_list_of_a_portable_zip(tmp_path, sep):
    """Compress-Archive puts the program in one folder, Hadir/, with portable.txt beside Hadir.exe."""
    z = tmp_path / "Hadir-Portable-1.3.0.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for rel, data in {**PROGRAM, "portable.txt": b"data next to Hadir.exe"}.items():
            f.writestr("Hadir" + sep + rel.replace("/", sep), data)
    assert uf.zip_files(z) == entries(PROGRAM)


def test_a_damaged_zip_is_noticed(tmp_path):
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as f:
        f.writestr("Hadir/Hadir.exe", b"A" * 1000)
    data = bytearray(z.read_bytes())
    data[data.index(b"A" * 100) + 10] = ord("B")
    z.write_bytes(bytes(data))
    with pytest.raises(zipfile.BadZipFile):
        uf.zip_files(z)


def test_plan_carries_what_changed_and_checks_the_rest():
    base = entries(PROGRAM)
    target = entries({**PROGRAM, "Hadir.exe": b"exe 2", "_internal/new.dll": b"new"})
    del target["_internal/old.pyd"]
    plan = uf.make_plan(target, [("1.3.0", base)])
    assert plan.copy == ["Hadir.exe", "_internal/new.dll"]
    assert plan.check == ["_internal/PySide6/Qt6Core.dll", "_internal/base_library.zip", "_internal/models/arcface.onnx"]
    assert plan.delete == ["_internal/old.pyd"] and plan.bases == ["1.3.0"]


def test_plan_against_two_versions_carries_every_difference():
    older = entries({**PROGRAM, "_internal/PySide6/Qt6Core.dll": b"qt 6.11"})
    base = entries(PROGRAM)
    target = entries({**PROGRAM, "Hadir.exe": b"exe 3"})
    plan = uf.make_plan(target, [("1.3.1", base), ("1.3.0", older)])
    assert plan.copy == ["Hadir.exe", "_internal/PySide6/Qt6Core.dll"]
    assert "_internal/models/arcface.onnx" in plan.check and plan.delete == []


def test_a_file_renamed_by_case_is_not_deleted():
    """Windows ignores case: deleting Old.PYD would delete the new old.pyd."""
    base = entries({**PROGRAM, "_internal/Old.PYD": b"x"})
    del base["_internal/old.pyd"]
    plan = uf.make_plan(entries(PROGRAM), [("1.3.0", base)])
    assert plan.delete == [] and "_internal/old.pyd" in plan.copy


def test_older_versions_join_only_while_the_update_stays_small():
    target = entries({**PROGRAM, "Hadir.exe": b"exe 3"})
    near = entries(PROGRAM)
    far = entries({**PROGRAM, "_internal/models/arcface.onnx": b"other model"})
    chosen = uf.choose_bases(target, [("1.2.0", far), ("1.3.0", near)], limit=100)
    assert [v for v, _ in chosen] == ["1.3.0"]
    chosen = uf.choose_bases(target, [("1.2.0", far), ("1.3.0", near)], limit=10_000)
    assert [v for v, _ in chosen] == ["1.3.0", "1.2.0"]


def test_versions_sort_as_numbers():
    found = [("1.9.0", {}), ("1.10.0", {}), ("1.2.0", {})]
    assert uf.choose_bases({}, found)[0][0] == "1.10.0"


def test_update_folder_for_inno_setup(tmp_path):
    files = {**PROGRAM, "Hadir.exe": b"exe 2", "_internal/{odd}/x.dll": b"x"}
    dist = make_dist(tmp_path / "Hadir", files)
    target = uf.scan(dist)
    uf.write_manifest(dist / uf.MANIFEST, "1.3.1", target)
    base = entries({**PROGRAM, "_internal/{odd}/gone.dll": b"g"})
    plan = uf.make_plan(target, [("1.3.0", base)])
    out = tmp_path / "update"
    (out / "payload").mkdir(parents=True)
    (out / "payload" / "stale.txt").write_text("from an earlier run")
    uf.write_update(dist, target, plan, out)
    payload = sorted(p.relative_to(out / "payload").as_posix() for p in (out / "payload").rglob("*") if p.is_file())
    assert payload == sorted(["Hadir.exe", "_internal/{odd}/x.dll", uf.MANIFEST])
    assert (out / "payload" / "Hadir.exe").read_bytes() == b"exe 2"
    check = (out / "check.txt").read_bytes().decode("ascii")
    lines = check.split("\r\n")
    assert lines[-1] == "" and len(lines) - 1 == len(plan.check) == 4
    # update.iss reads the hash from columns 1-64 and the path from column 66
    first = lines[0]
    assert first[64] == " " and first[:64] == target[plan.check[0]][0]
    assert first[65:] == plan.check[0].replace("/", "\\")
    delete = (out / "delete.iss").read_text(encoding="utf-8-sig").splitlines()
    assert delete == ["[InstallDelete]", 'Type: files; Name: "{app}\\_internal\\{{odd}\\gone.dll"']
    info = (out / "info.iss").read_bytes()
    assert info.startswith(b"\xef\xbb\xbf")  # Inno Setup reads UTF-8 only with a BOM
    text = info.decode("utf-8-sig")
    assert '#define UpdateFrom "1.3.0"' in text and '#define UpdateFiles "2"' in text
    assert '#define UpdateFromText "الإصدار 1.3.0"' in text and '#define UpdateChecked "4"' in text


def test_file_names_must_be_ascii_for_the_checks(tmp_path):
    files = {**PROGRAM, "_internal/ملف.txt": b"x"}
    dist = make_dist(tmp_path / "Hadir", files)
    target = uf.scan(dist)
    with pytest.raises(ValueError):
        uf.write_update(dist, target, uf.make_plan(target, [("1.3.0", target)]), tmp_path / "u")


def test_from_text():
    assert uf.from_text(["1.3.0"]) == "الإصدار 1.3.0"
    assert uf.from_text(["1.4.0", "1.3.1", "1.3.0"]) == "الإصدارات 1.4.0، 1.3.1 و1.3.0"


def test_verify_finds_missing_and_changed_files(tmp_path):
    dist = make_dist(tmp_path / "Hadir", PROGRAM)
    files = uf.scan(dist)
    assert uf.verify(dist, files) == []
    (dist / "Hadir.exe").write_bytes(b"exe X")  # same size, other bytes
    (dist / "_internal" / "old.pyd").unlink()
    assert uf.verify(dist, files) == ["different: Hadir.exe", "missing: _internal/old.pyd"]


def test_command_line_end_to_end(tmp_path, monkeypatch, capsys):
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    dist = make_dist(tmp_path / "Hadir", {**PROGRAM, "Hadir.exe": b"exe 2"})
    bases = tmp_path / "bases"
    bases.mkdir()
    assert uf.main(["manifest", str(dist), "1.3.1"]) == 0
    assert uf.main(["plan", str(dist), str(bases), str(tmp_path / "u")]) == 0  # no earlier release
    assert not (tmp_path / "u").exists() and out_file.read_text() == "from=\n"
    uf.write_manifest(bases / "1.3.0.txt", "1.3.0", entries(PROGRAM))
    assert uf.main(["plan", str(dist), str(bases), str(tmp_path / "u")]) == 0
    assert (tmp_path / "u" / "payload" / "Hadir.exe").exists()
    assert out_file.read_text(encoding="utf-8").endswith("from=1.3.0\nfrom_text=الإصدار 1.3.0\n")
    assert uf.main(["verify", str(dist), str(dist / uf.MANIFEST)]) == 0
    (dist / "Hadir.exe").write_bytes(b"x")
    assert uf.main(["verify", str(dist), str(dist / uf.MANIFEST)]) == 1
    assert "different: Hadir.exe" in capsys.readouterr().out


class FakeGitHub:
    """Serves a release listing and its assets in place of api.github.com."""

    def __init__(self, releases, blobs):
        self.releases, self.blobs, self.calls = releases, blobs, []

    def __call__(self, url, token, accept="application/vnd.github+json"):
        self.calls.append((url, token, accept))
        if "/releases?" in url:
            return io.BytesIO(json.dumps(self.releases).encode())
        return io.BytesIO(self.blobs[url])


def asset(name, data, token):
    url = f"https://api.github.com/assets/{name}" if token else f"https://github.com/download/{name}"
    return {"name": name, "size": len(data), "url": f"https://api.github.com/assets/{name}",
            "browser_download_url": f"https://github.com/download/{name}"}, {url: data}


@pytest.mark.parametrize("token", ["", "secret"])
def test_earlier_releases_are_found_on_github(tmp_path, monkeypatch, token):
    lists = tmp_path / "lists"
    lists.mkdir()
    uf.write_manifest(lists / "l.txt", "1.2.0", entries(PROGRAM))
    listed = (lists / "l.txt").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as f:
        for rel, data in {**PROGRAM, "portable.txt": b"p"}.items():
            f.writestr("Hadir/" + rel, data)
    blobs = {}
    a120, b = asset("Hadir-files-1.2.0.txt", listed, token)
    blobs.update(b)
    a130, b = asset("Hadir-Portable-1.3.0.zip", buf.getvalue(), token)
    blobs.update(b)
    a110, b = asset("Hadir-Portable-1.1.0.zip", buf.getvalue(), token)
    blobs.update(b)
    releases = [
        {"tag_name": "v1.4.0", "draft": False, "prerelease": False, "assets": []},  # not before 1.3.1
        {"tag_name": "v1.3.1", "draft": False, "prerelease": False, "assets": []},  # the version being built
        {"tag_name": "v1.3.0", "draft": False, "prerelease": False, "assets": [a130]},
        {"tag_name": "v1.2.9", "draft": True, "prerelease": False, "assets": [a120]},
        {"tag_name": "v1.2.0", "draft": False, "prerelease": False, "assets": [a120]},
        {"tag_name": "v1.1.0", "draft": False, "prerelease": False, "assets": [a110]},  # no list, not the latest
        {"tag_name": "nightly", "draft": False, "prerelease": False, "assets": []},
    ]
    releases.append({"tag_name": "v1.0.0", "draft": False, "prerelease": False, "assets": []})
    known = tmp_path / "known"  # lists kept in the repository for releases made before there were any
    known.mkdir()
    uf.write_manifest(known / "1.0.0.txt", "1.0.0", entries({**PROGRAM, "Hadir.exe": b"exe 0"}))
    fake = FakeGitHub(releases, blobs)
    monkeypatch.setattr(uf, "_open", fake)
    out = tmp_path / "bases"
    assert uf.fetch_bases("o/r", "1.3.1", out, token, known) == ["1.3.0", "1.2.0", "1.0.0"]
    assert sorted(p.name for p in out.iterdir()) == ["1.0.0.txt", "1.2.0.txt", "1.3.0.txt"]
    assert uf.read_manifest(out / "1.3.0.txt") == ("1.3.0", entries(PROGRAM))
    assert uf.read_manifest(out / "1.2.0.txt") == ("1.2.0", entries(PROGRAM))
    assert uf.read_manifest(out / "1.0.0.txt")[1]["Hadir.exe"] == entries({"x": b"exe 0"})["x"]
    downloads = [c for c in fake.calls if "/releases?" not in c[0]]
    assert [c[2] for c in downloads] == ["application/octet-stream"] * 2
    assert all(c[0].startswith("https://api.github.com/" if token else "https://github.com/") for c in downloads)


def test_an_incomplete_download_is_retried(tmp_path, monkeypatch):
    data = b"0123456789"
    sent = []

    def flaky(url, token, accept=""):
        sent.append(url)
        return io.BytesIO(data[:5] if len(sent) == 1 else data)

    monkeypatch.setattr(uf, "_open", flaky)
    monkeypatch.setattr(uf.time, "sleep", lambda s: None)
    dest = tmp_path / "f"
    uf._download({"name": "f", "size": len(data), "url": "u", "browser_download_url": "b"}, dest, "")
    assert dest.read_bytes() == data and len(sent) == 2


def test_the_kept_file_lists_of_the_first_releases():
    """packaging/manifests: the releases before 1.3.1 published no file list; these were made from
    their portable zips (checked against the published SHA256SUMS)."""
    folder = Path(uf.__file__).parent / "manifests"
    lists = {p.stem: uf.read_manifest(p) for p in folder.glob("*.txt")}
    assert sorted(lists, key=uf.version_key) == ["1.0.0", "1.0.1", "1.1.0", "1.2.0", "1.3.0"]
    assert all(version == stem for stem, (version, _) in lists.items())
    files = {v: f for v, (_, f) in lists.items()}
    # the same Python and libraries since 1.0.1: only the program and two build files differ
    differ = {p for p in files["1.3.0"] if files["1.0.1"].get(p) != files["1.3.0"][p]}
    assert differ == {"Hadir.exe", "_internal/base_library.zip", "_internal/numpy-2.5.4.dist-info/RECORD"}
    assert files["1.3.0"]["_internal/models/arcfaceresnet100-8.onnx"][1] > 200_000_000
