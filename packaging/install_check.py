"""Install, update and remove Hadir on the Windows build runner, the way the door laptop does it.

  python packaging/install_check.py --setup dist/HadirSetup-1.3.1.exe --manifest dist/Hadir/hadir-files.txt
      [--update dist/HadirUpdate-1.3.1.exe --check build/update/check.txt --previous HadirSetup-1.3.0.exe]
      [--face face.jpg]

With an update file: it refuses a computer without Hadir; the previous release is installed and
started; the update closes it and leaves exactly the files of the new version, which then passes its
self-test; a damaged file the update does not carry makes it refuse without changing anything.
Then the full installer goes over the running program and repairs it, and the uninstaller removes
the running program.
"""

import argparse
import subprocess
import sys
import time
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_files import read_manifest, verify  # noqa: E402

KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B0F6D5C-3C1B-4A7E-9F4B-2D8E1A9C7F21}_is1"
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
CANNOT_PROCEED = 7  # Inno Setup: the "Preparing to Install" step found that Setup cannot go on
LOGS = Path("install-logs")


def installed():
    """(folder, version) of the installed Hadir, or (None, None)."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return Path(winreg.QueryValueEx(k, "InstallLocation")[0]), winreg.QueryValueEx(k, "DisplayVersion")[0]
    except OSError:
        return None, None


def step(text: str) -> None:
    print(f"\n=== {text}", flush=True)


def run_setup(exe: Path, name: str, expect: int = 0) -> None:
    LOGS.mkdir(exist_ok=True)
    log = (LOGS / f"{name}.log").resolve()
    started = time.monotonic()
    code = subprocess.run([str(exe), *SILENT, f"/LOG={log}"], timeout=1800).returncode
    print(f"{exe.name}: exit code {code} after {time.monotonic() - started:.0f} s", flush=True)
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    for line in text.splitlines():
        if "missing or different" in line or "checked " in line or "Hadir is running" in line:
            print("  log:", line.split(" ", 2)[-1])
    if code != expect:
        print(text[-10000:])
        raise SystemExit(f"{exe.name}: exit code {code}, expected {expect}")


def start_hadir(app: Path) -> subprocess.Popen:
    """The program running, like the attendance screen at the door, holding its files open."""
    proc = subprocess.Popen([str(app / "Hadir.exe"), "--demo"])
    time.sleep(15)
    if proc.poll() is None:
        print("Hadir.exe is running", flush=True)
    else:
        print(f"Hadir.exe ended by itself (exit code {proc.returncode})", flush=True)
    return proc


def ended(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise SystemExit("Hadir.exe was still running after the installer had finished")
    print(f"Hadir.exe was closed (exit code {proc.returncode})", flush=True)


def check_files(files: dict, version: str) -> Path:
    app, current = installed()
    if app is None or current != version:
        raise SystemExit(f"installed version {current}, expected {version}")
    problems = verify(app, files)
    for line in problems[:20]:
        print(line)
    if problems:
        raise SystemExit(f"{len(problems)} files in {app} differ from the build")
    print(f"{app}: all {len(files)} files of {version} as built", flush=True)
    return app


def selftest(app: Path, face: str) -> None:
    out = (LOGS / "selftest.txt").resolve()
    cmd = [str(app / "Hadir.exe"), "--selftest", "--selftest-out", str(out)]
    if face:
        cmd += ["--selftest-image", str(Path(face).resolve())]
    code = subprocess.run(cmd, timeout=900).returncode
    print(out.read_text(encoding="utf-8") if out.exists() else "(no self-test output)", flush=True)
    if code:
        raise SystemExit(f"self-test of the installed program failed ({code})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--update", type=Path)
    ap.add_argument("--check", type=Path)
    ap.add_argument("--previous", type=Path)
    ap.add_argument("--face", default="")
    args = ap.parse_args()
    version, files = read_manifest(args.manifest)
    if installed()[0] is not None:
        raise SystemExit("Hadir is already installed on this runner")

    if args.update:
        step("the update file on a computer without Hadir: refused")
        run_setup(args.update, "update-without-hadir", expect=CANNOT_PROCEED)
        if installed()[0] is not None:
            raise SystemExit("the refused update installed something")

        step(f"previous release: {args.previous.name}")
        run_setup(args.previous, "previous")
        app, old = installed()
        print(f"Hadir {old} installed in {app}")
        hadir = start_hadir(app)

        step("the update file over the running previous release")
        run_setup(args.update, "update")
        ended(hadir)
        app = check_files(files, version)
        selftest(app, args.face)

        step("a damaged file that the update does not carry: refused, nothing changed")
        kept = [line[65:].replace("\\", "/") for line in args.check.read_text(encoding="ascii").splitlines()]
        victim = app / min((p for p in kept if files[p][1] > 0), key=lambda p: files[p][1])
        damaged = victim.read_bytes() + b"damaged"
        victim.write_bytes(damaged)
        run_setup(args.update, "update-damaged", expect=CANNOT_PROCEED)
        if victim.read_bytes() != damaged or installed()[1] != version:
            raise SystemExit("the refused update changed the installed program")
        print(f"{victim.relative_to(app)} left as it was")

    step("the full installer over the running program: repairs it")
    app = installed()[0]
    hadir = start_hadir(app) if app else None
    run_setup(args.setup, "setup")
    if hadir:
        ended(hadir)
    app = check_files(files, version)
    if not args.update:
        selftest(app, args.face)

    step("uninstall while the program is running")
    hadir = start_hadir(app)
    uninstaller = app / "unins000.exe"
    subprocess.run([str(uninstaller), *SILENT[:3], f"/LOG={(LOGS / 'uninstall.log').resolve()}"], timeout=600)
    deadline = time.monotonic() + 300
    while (installed()[0] is not None or (app / "Hadir.exe").exists()) and time.monotonic() < deadline:
        time.sleep(2)  # the uninstaller goes on from a copy of itself in the temporary folder
    ended(hadir)
    if installed()[0] is not None or (app / "Hadir.exe").exists():
        raise SystemExit("the uninstaller did not remove Hadir")
    print("removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
