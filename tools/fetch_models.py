"""Download the large face-recognition models (once, on a computer with internet).

The program itself never connects to the internet: run this before building the
Windows package (the CI build does it automatically).  Every file is verified
against the SHA-256 pinned in hadir/face/models.py.

    python tools/fetch_models.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hadir.face import models  # noqa: E402


def download(spec: models.ModelSpec, target: Path) -> None:
    tmp = target.with_suffix(target.suffix + ".part")
    print(f"downloading {spec.filename} ({spec.size / 1e6:.0f} MB) …", flush=True)
    with urllib.request.urlopen(spec.url, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    digest = models.sha256_of(tmp)
    if digest != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"checksum mismatch for {spec.filename}: {digest}")
    tmp.replace(target)


def main() -> int:
    folder = models.models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    failed = False
    for spec in models.ALL_MODELS:
        path = folder / spec.filename
        if path.is_file() and path.stat().st_size == spec.size and models.sha256_of(path) == spec.sha256:
            print(f"ok       {spec.filename}")
            continue
        if not spec.url:
            print(f"MISSING  {spec.filename} (part of the repository; restore it from git)")
            failed = True
            continue
        download(spec, path)
        print(f"fetched  {spec.filename}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
