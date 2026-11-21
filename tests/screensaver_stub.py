"""Stands in for a Linux desktop's screen saver on a private session bus (see test_awake.py)."""

from PySide6.QtCore import ClassInfo, QCoreApplication, QObject, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection


@ClassInfo({"D-Bus Interface": "org.freedesktop.ScreenSaver"})
class ScreenSaver(QDBusAbstractAdaptor):
    @Slot(str, str, result="uint")
    def Inhibit(self, app, reason):
        print("INHIBIT", app, reason, flush=True)
        return 7


app = QCoreApplication([])
root = QObject()
ScreenSaver(root)
bus = QDBusConnection.sessionBus()
if bus.registerService("org.freedesktop.ScreenSaver") and bus.registerObject("/org/freedesktop/ScreenSaver", root):
    print("ready", flush=True)
app.exec()
