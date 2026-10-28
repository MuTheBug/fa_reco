"""Application settings: defaults and Arabic labels."""

from __future__ import annotations

DEFAULTS: dict = {
    # Organisation
    "org.name": "الدائرة",
    "org.subtitle": "",
    "org.logo": "",  # base64 PNG
    # Display
    "ui.arabic_digits": False,
    "ui.month_style": "levant",
    "ui.font_scale": 1.0,
    # Camera
    "camera.index": 0,
    "camera.backend": "auto",  # auto | dshow | msmf | any
    "camera.width": 1280,
    "camera.height": 720,
    "camera.mirror": True,
    "camera.source": "",  # optional video file / image folder instead of a camera
    # Recognition
    "recog.engine": "dual",  # dual | arcface | sface
    "recog.security": "high",  # standard | high | max
    "recog.liveness": "auto",  # off | auto | always
    "recog.adaptive": True,
    "recog.min_eye_px": 40,
    # Kiosk
    "kiosk.duplicate_minutes": 5,
    "kiosk.show_recent": True,
    "kiosk.show_names_recent": True,
    "kiosk.sound": True,
    "kiosk.save_snapshots": True,
    "kiosk.snapshot_days": 180,
    "kiosk.allow_pin": False,
    "kiosk.fill_light": True,
    "kiosk.result_seconds": 3,
    "kiosk.admin_timeout_minutes": 5,
    "kiosk.start_in_kiosk": True,
    # Attendance rules
    "attendance.day_start": "04:00",
    "attendance.dedupe_seconds": 120,
    "attendance.low_liveness": 0.5,
    # Backup
    "backup.auto": True,
    "backup.dir": "",
    "backup.keep": 30,
    "backup.include_snapshots": False,
    # Reports
    "reports.signatures": ["منظّم الكشف", "مسؤول الموارد البشرية", "المدير"],
    # Integrity
    "security.clock_check": True,
}

SECURITY_LEVELS = {
    # primary (ArcFace) threshold, margin to the runner-up, frames, SFace-only threshold
    "standard": dict(arc=0.40, margin=0.08, frames=2, sface=0.45, sface_margin=0.08),
    "high": dict(arc=0.45, margin=0.10, frames=2, sface=0.50, sface_margin=0.10),
    "max": dict(arc=0.50, margin=0.12, frames=3, sface=0.55, sface_margin=0.12),
}

SECURITY_LABELS = {"standard": "عادي", "high": "عالٍ (موصى به)", "max": "أقصى"}
ENGINE_LABELS = {
    "dual": "تحقق مزدوج بمحرّكين (أعلى دقة)",
    "arcface": "ArcFace فقط",
    "sface": "SFace فقط (أسرع، أقل دقة)",
}
LIVENESS_LABELS = {
    "off": "معطّل",
    "auto": "تلقائي: فحص صامت، وطلب حركة رأس عند الشك (موصى به)",
    "always": "اطلب حركة الرأس في كل مرة",
}
