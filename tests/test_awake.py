"""The attendance screen keeps a Linux desktop from locking the screen while it is shown."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktops only")

CLIENT = '''
import subprocess, sys, time
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection
app = QCoreApplication([])
from hadir.ui.awake import CONNECTION, KeepAwake
stub = subprocess.Popen([sys.executable, sys.argv[1]], stdout=subprocess.PIPE, text=True)
assert stub.stdout.readline().startswith("ready")
keep = KeepAwake()
on = keep.on()
me = QDBusConnection(CONNECTION).baseService()
inhibit = stub.stdout.readline().strip()
keep.off()
time.sleep(0.3)
names = QDBusConnection.sessionBus().interface().registeredServiceNames()
names = names.value() if hasattr(names, "value") else names
print(inhibit)
print(f"RESULT on={on} active={keep.active} gone={me not in names}")
stub.terminate()
'''


def test_without_a_desktop_nothing_happens(monkeypatch, qapp):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/hadir-test-bus")
    from hadir.ui.awake import KeepAwake

    keep = KeepAwake()
    assert keep.on() is False and not keep.active
    keep.off()


@pytest.mark.skipif(shutil.which("dbus-run-session") is None, reason="needs dbus-run-session")
def test_the_screen_lock_is_held_off_while_the_attendance_screen_shows(tmp_path):
    client = tmp_path / "client.py"
    client.write_text(CLIENT)
    env = dict(os.environ, PYTHONPATH=str(HERE.parent), QT_QPA_PLATFORM="offscreen")
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    out = subprocess.run(["dbus-run-session", "--", sys.executable, str(client), str(HERE / "screensaver_stub.py")],
                         capture_output=True, text=True, timeout=60, env=env).stdout
    assert "INHIBIT Hadir شاشة الحضور تعمل" in out
    # lifted when leaving the attendance screen: the connection that asked is closed
    assert "RESULT on=True active=False gone=True" in out
