# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Windows:  pyinstaller packaging/hadir.spec --noconfirm
from pathlib import Path

import PySide6
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
QT_TRANSLATIONS = Path(PySide6.__file__).parent / "Qt" / "translations"

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
    hiddenimports=collect_submodules("hadir"),
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
