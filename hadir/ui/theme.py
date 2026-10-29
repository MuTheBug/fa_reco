"""Colours, style sheet and icons."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ..paths import resource_dir

C = {
    "primary": "#1f5fa8",
    "primary_dark": "#174b87",
    "primary_light": "#e8f0fa",
    "navy": "#0f2c4c",
    "navy_2": "#16385f",
    "bg": "#f3f5f8",
    "card": "#ffffff",
    "border": "#dde3ea",
    "text": "#1f2937",
    "muted": "#6b7280",
    "ok": "#15803d",
    "ok_bg": "#e3f5e8",
    "warn": "#b45309",
    "warn_bg": "#fff1d6",
    "bad": "#b91c1c",
    "bad_bg": "#fde2e2",
    "info": "#1d4ed8",
    "info_bg": "#e3eefc",
    "muted_bg": "#f1f3f5",
}

TONE_BG = {"bad": C["bad_bg"], "warn": C["warn_bg"], "info": C["info_bg"], "ok": C["ok_bg"],
           "muted": C["muted_bg"]}
TONE_FG = {"bad": C["bad"], "warn": C["warn"], "info": C["info"], "ok": C["ok"], "muted": C["muted"]}

FONT_FAMILY = "Noto Sans Arabic"


def stylesheet(scale: float = 1.0) -> str:
    fs = round(10 * scale, 1)
    icons = resource_dir() / "icons"
    down = (icons / "chevron-down.svg").as_posix()
    up = (icons / "chevron-up.svg").as_posix()
    return f"""
    QWidget {{ font-family: "{FONT_FAMILY}", "Segoe UI", "Tahoma"; font-size: {fs}pt; color: {C['text']}; }}
    QMainWindow, QDialog {{ background: {C['bg']}; }}
    QToolTip {{ background: {C['navy']}; color: white; border: none; padding: 6px; }}

    #Sidebar {{ background: {C['navy']}; }}
    #Sidebar QPushButton {{
        color: #dbe5f1; background: transparent; border: none; border-radius: 8px;
        text-align: right; padding: 9px 14px; font-size: {fs + 0.5}pt;
    }}
    #Sidebar QPushButton:hover {{ background: {C['navy_2']}; color: white; }}
    #Sidebar QPushButton:checked {{ background: {C['primary']}; color: white; }}
    #SidebarTitle {{ color: white; font-size: {fs + 6}pt; font-weight: bold; }}
    #SidebarSub {{ color: #9fb3c8; font-size: {fs - 1}pt; }}
    #SidebarUser {{ color: #c7d4e2; font-size: {fs - 0.5}pt; }}

    #PageTitle {{ font-size: {fs + 6}pt; font-weight: bold; color: {C['navy']}; }}
    #PageSubtitle {{ color: {C['muted']}; }}
    #Card {{ background: {C['card']}; border: 1px solid {C['border']}; border-radius: 12px; }}
    #CardTitle {{ font-weight: bold; color: {C['navy']}; font-size: {fs + 1}pt; }}
    #StatValue {{ font-size: {fs + 14}pt; font-weight: bold; }}
    #StatLabel {{ color: {C['muted']}; }}
    #Hint {{ color: {C['muted']}; font-size: {fs - 0.5}pt; }}
    #Badge {{ border-radius: 9px; padding: 2px 10px; font-size: {fs - 1}pt; }}

    QPushButton {{
        background: white; border: 1px solid {C['border']}; border-radius: 8px;
        padding: 7px 14px; min-height: 20px;
    }}
    QPushButton:hover {{ background: {C['primary_light']}; border-color: #b8cde6; }}
    QPushButton:pressed {{ background: #d6e4f5; }}
    QPushButton:disabled {{ color: #a0a7b1; background: #f5f6f8; }}
    QPushButton[kind="primary"] {{ background: {C['primary']}; color: white; border: none; font-weight: bold; }}
    QPushButton[kind="primary"]:hover {{ background: {C['primary_dark']}; }}
    QPushButton[kind="primary"]:disabled {{ background: #9db7d6; color: #eef3f9; }}
    QPushButton[kind="danger"] {{ color: {C['bad']}; border-color: #f0c4c4; }}
    QPushButton[kind="danger"]:hover {{ background: {C['bad_bg']}; }}
    QPushButton[kind="ghost"] {{ background: transparent; border: none; color: {C['primary']}; }}
    QPushButton[kind="ghost"]:hover {{ background: {C['primary_light']}; }}

    QLineEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
        background: white; border: 1px solid #cdd6e1; border-radius: 7px; padding: 5px 8px;
        selection-background-color: {C['primary']};
    }}
    QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {C['primary']}; }}
    QComboBox QAbstractItemView {{ background: white; selection-background-color: {C['primary_light']};
        selection-color: {C['text']}; border: 1px solid {C['border']}; }}
    QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{down}"); width: 12px; height: 12px; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button {{ border: none; width: 18px; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url("{up}"); width: 10px; height: 10px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}

    QTableView {{
        background: white; border: 1px solid {C['border']}; border-radius: 10px;
        gridline-color: #edf0f4; alternate-background-color: #f8fafc;
        selection-background-color: #d5e4f6; selection-color: {C['text']};
    }}
    QHeaderView::section {{
        background: #eef2f7; color: {C['navy']}; font-weight: bold; border: none;
        border-bottom: 1px solid {C['border']}; padding: 7px 6px;
    }}
    QTableView QTableCornerButton::section {{ background: #eef2f7; border: none; }}
    QListWidget {{ background: white; border: 1px solid {C['border']}; border-radius: 10px; padding: 4px; }}
    QListWidget::item {{ padding: 6px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: #d5e4f6; color: {C['text']}; }}

    QTabWidget::pane {{ border: 1px solid {C['border']}; border-radius: 10px; background: white; top: -1px; }}
    QTabBar::tab {{ background: transparent; padding: 8px 18px; border: none; color: {C['muted']}; }}
    QTabBar::tab:selected {{ color: {C['primary']}; font-weight: bold; border-bottom: 3px solid {C['primary']}; }}
    QTabBar::tab:hover {{ color: {C['text']}; }}

    QGroupBox {{ background: white; border: 1px solid {C['border']}; border-radius: 10px;
        margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: bold; color: {C['navy']}; }}
    QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top right; padding: 0 8px; right: 12px; }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: #c5ced9; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: #c5ced9; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QProgressBar {{ border: none; background: #e5e9ef; border-radius: 5px; height: 10px; text-align: center; }}
    QProgressBar::chunk {{ background: {C['primary']}; border-radius: 5px; }}
    QMenu {{ background: white; border: 1px solid {C['border']}; padding: 4px; }}
    QMenu::item {{ padding: 6px 20px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {C['primary_light']}; }}
    """


def apply_theme(app, scale: float = 1.0) -> None:
    app.setStyle("Fusion")
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor(C["bg"]))
    pal.setColor(QPalette.Base, QColor("white"))
    pal.setColor(QPalette.Text, QColor(C["text"]))
    pal.setColor(QPalette.Highlight, QColor(C["primary"]))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(pal)
    font = QFont(FONT_FAMILY)
    font.setPointSizeF(10 * scale)
    app.setFont(font)
    app.setStyleSheet(stylesheet(scale))


# ---------------------------------------------------------------------------
# Icons: simple stroke drawings on a 24x24 grid (drawn for this program).
_S = 'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
ICONS = {
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/>'
             '<circle cx="17" cy="9" r="2.6"/><path d="M16.5 14.2c2.6.3 4.5 2.4 4.5 5.3"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/>'
                '<path d="M8 3v4"/><path d="M16 3v4"/>',
    "schedule": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/>'
                '<path d="M16 3v4"/><path d="M12 13v3l2 1.5"/>',
    "leave": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5h6v2"/><path d="M3 12h18"/>',
    "holiday": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/>'
               '<path d="M16 3v4"/><path d="M12 13l1 2 2 .3-1.5 1.4.4 2.1-1.9-1-1.9 1 .4-2.1L9 15.3l2-.3z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "report": '<path d="M6 2h9l5 5v15H6z"/><path d="M15 2v5h5"/><path d="M9 12h7"/><path d="M9 16h7"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "settings": '<path d="M4 6h9"/><path d="M19 6h1"/><circle cx="16" cy="6" r="2.2"/><path d="M4 12h3"/>'
                '<path d="M13 12h7"/><circle cx="10" cy="12" r="2.2"/><path d="M4 18h11"/>'
                '<circle cx="18" cy="18" r="2.2"/>',
    "face": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8"/><path d="M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8"/>'
            '<path d="M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16"/><path d="M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/>'
            '<path d="M9 10v.5"/><path d="M15 10v.5"/><path d="M9.5 15c1.4 1.2 3.6 1.2 5 0"/>',
    "kiosk": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8"/><path d="M12 16v4"/>',
    "logout": '<path d="M14 4h5v16h-5"/><path d="M10 8l-4 4 4 4"/><path d="M6 12h11"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    "print": '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
    "excel": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/>'
             '<path d="M9 3v18"/>',
    "pdf": '<path d="M6 2h9l5 5v15H6z"/><path d="M15 2v5h5"/><path d="M9 17v-4h1.5a1.5 1.5 0 0 1 0 3H9"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "upload": '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
    "download": '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M4 16v4h16v-4"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v4"/><path d="M12 17.5v.5"/>',
    "user_plus": '<circle cx="9" cy="8" r="3.5"/><path d="M3 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/>'
                 '<path d="M19 8v6"/><path d="M16 11h6"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    "up": '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14"/><path d="M6 13l6 6 6-6"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    "send": '<path d="M21 3L3 10.5l7.5 3L13.5 21z"/><path d="M21 3L10.5 13.5"/>',
}


@lru_cache(maxsize=512)
def _svg_pixmap(name: str, color: str, size: int) -> QPixmap:
    body = ICONS.get(name, ICONS["x"])
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="{size}" '
           f'height="{size}"><g {_S.format(c=color)}>{body}</g></svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size * 2, size * 2))
    p.end()
    pm.setDevicePixelRatio(2)
    return pm


def icon(name: str, color: str = C["text"], size: int = 20) -> QIcon:
    ic = QIcon(_svg_pixmap(name, color, size))
    return ic


def pixmap(name: str, color: str = C["text"], size: int = 20) -> QPixmap:
    return _svg_pixmap(name, color, size)
