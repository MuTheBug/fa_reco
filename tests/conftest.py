import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from hadir.fonts import register_fonts

    app = QApplication.instance() or QApplication([])
    register_fonts()
    return app
