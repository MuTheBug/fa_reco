"""Keep the screen on while the attendance screen is shown.

Linux desktops lock the screen after a few minutes without keyboard or mouse, and nobody touches
them at the door: the program asks the desktop not to (the freedesktop ScreenSaver inhibition,
honoured by KDE Plasma, Xfce, LXQt and others). The request is held on a connection of its own,
so closing that connection lifts it; the desktop also lifts it if the program stops.
Windows keeps its own power settings.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("hadir")
CONNECTION = "hadir-keep-awake"
PATH = "/org/freedesktop/ScreenSaver"


class KeepAwake:
    def __init__(self):
        self.active = False

    def on(self) -> bool:
        if self.active or not sys.platform.startswith("linux"):
            return self.active
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

            bus = QDBusConnection.connectToBus(QDBusConnection.SessionBus, CONNECTION)
            if bus.isConnected():
                screensaver = QDBusInterface("org.freedesktop.ScreenSaver", PATH,
                                             "org.freedesktop.ScreenSaver", bus)
                if screensaver.isValid():
                    reply = screensaver.call("Inhibit", "Hadir", "شاشة الحضور تعمل")
                    self.active = reply.type() == QDBusMessage.ReplyMessage
                del screensaver
            del bus
            if not self.active:
                QDBusConnection.disconnectFromBus(CONNECTION)
        except Exception:
            log.exception("keep awake")
            self.active = False
        return self.active

    def off(self) -> None:
        if not self.active:
            return
        self.active = False
        try:
            from PySide6.QtDBus import QDBusConnection

            QDBusConnection.disconnectFromBus(CONNECTION)  # the desktop lifts the request
        except Exception:
            log.exception("keep awake")
