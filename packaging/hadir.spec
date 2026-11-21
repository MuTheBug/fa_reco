# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for Windows and Linux:  pyinstaller packaging/hadir.spec --noconfirm
import sys
from pathlib import Path

import PySide6

WINDOWS = sys.platform == "win32"

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
    excludes=["tkinter", "pytest", "onnx", "readline", "curses", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtWebEngineCore"],
    noarchive=False,
)
if sys.platform.startswith("linux"):
    # Libraries every Linux desktop has, in a version at least as new as the build machine's.
    # A bundled older copy would be used instead of the system's and break what the system
    # loads into the program itself (graphics drivers need the newer libstdc++, input methods
    # the newer glib, the fonts configuration the newer fontconfig, ...). The GTK look is not
    # used (the program draws its own), so its plugin and libraries stay out as well.
    SYSTEM_LIBRARIES = (
        "libstdc++.so", "libgcc_s.so", "libz.so", "libexpat.so", "libuuid.so", "libdbus-1.so",
        "libffi.so", "liblzma.so", "libbz2.so", "libreadline", "libtinfo", "libncurses",
        "libX", "libxcb", "libxkbcommon", "libwayland-", "libdrm.so", "libEGL", "libGL", "libOpenGL", "libgbm",
        "libfontconfig", "libfreetype", "libharfbuzz", "libbrotli", "libpng16", "libjpeg",
        "libglib-2.0", "libgobject-2.0", "libgio-2.0", "libgmodule-2.0", "libgthread-2.0", "libpcre2",
        "libmount", "libblkid", "libselinux", "libsystemd", "libcap.so", "liblz4", "libzstd", "libgcrypt",
        "libgpg-error", "libbsd", "libmd.so",
        "libgtk-3", "libgdk-3", "libgdk_pixbuf", "libcairo", "libpango", "libatk", "libatspi", "libepoxy",
        "libfribidi", "libthai", "libdatrie", "libgraphite2", "libpixman",
        "libcups", "libavahi", "libgnutls", "libgssapi_krb5", "libkrb5", "libk5crypto", "libkeyutils",
        "libcom_err", "libp11-kit", "libtasn1", "libnettle", "libhogweed", "libgmp", "libidn2", "libunistring",
    )

    def bundled(entry):
        dest, source = entry[0], entry[1]
        if dest.replace("\\", "/").endswith("platformthemes/libqgtk3.so"):
            return False
        return not (source.startswith(("/usr/lib", "/lib")) and Path(dest).name.startswith(SYSTEM_LIBRARIES))

    a.binaries = [b for b in a.binaries if bundled(b)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Hadir",
    icon=str(ROOT / "hadir" / "resources" / "icons" / "hadir.ico") if WINDOWS else None,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Hadir", upx=False)
