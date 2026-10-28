# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Windows:  pyinstaller packaging/hadir.spec --noconfirm
from pathlib import Path

import PySide6

ROOT = Path(SPECPATH).parent
QT_TRANSLATIONS = Path(PySide6.__file__).parent / "Qt" / "translations"


def source_modules(package):
    """Every module of the package, listed from its source files.

    The admin pages are imported by name, so the build must be told about them.
    collect_submodules() cannot do it: it imports the package, and the project
    folder is not on sys.path while this file runs, so it silently found nothing.
    """
    names = []
    for path in sorted((ROOT / package).rglob("*.py")):
        parts = path.relative_to(ROOT).with_suffix("").parts
        if parts[-1] == "__main__":
            continue
        names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


datas = [
    (str(ROOT / "hadir" / "resources"), "hadir/resources"),
    (str(ROOT / "LICENSES"), "LICENSES"),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]
for model in sorted((ROOT / "models").glob("*.onnx")):
    if model.name != "arcfaceresnet100-11-int8.onnx":
        datas.append((str(model), "models"))
for name in ("qtbase_ar.qm", "qt_ar.qm"):
    qm = QT_TRANSLATIONS / name
    if qm.exists():
        datas.append((str(qm), "hadir/resources/i18n"))

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=source_modules("hadir"),
    excludes=["tkinter", "pytest", "onnx", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hadir",
    icon=str(ROOT / "hadir" / "resources" / "icons" / "hadir.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Hadir", upx=False)
