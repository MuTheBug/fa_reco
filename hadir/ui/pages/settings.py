"""Settings, users, backup/restore and about."""

from __future__ import annotations

import base64
import sys
import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import APP_SUBTITLE, APP_TITLE, __version__
from ...core import maintenance
from ...core.security import ROLES
from ...core.settings import ENGINE_LABELS, LIVENESS_LABELS, SECURITY_LABELS
from ...core.timeutil import Fmt, digits
from ...db.store import StoreError
from ...face import models
from ...face.camera import list_cameras
from ...notify import telegram
from ...paths import backups_dir, data_dir, db_path, snapshots_dir
from ..admin import Page
from ..theme import C, apply_theme
from .schedules import RulesOverview
from ..widgets.camera_view import CameraView
from ..widgets.common import (DataTable, PageHeader, SpinBox, button, confirm, digit_locale, error,
                              info, label)


class _Job(QObject):
    done = Signal(object)


def run_in_background(parent, fn, on_done) -> None:
    """Run ``fn`` in a thread; ``on_done(result or exception)`` is called in the GUI thread."""
    job = _Job(parent)
    job.done.connect(on_done)
    job.done.connect(lambda _: job.deleteLater())

    def work():
        try:
            result = fn()
        except Exception as exc:
            result = exc
        job.done.emit(result)

    threading.Thread(target=work, daemon=True).start()


def valid_hhmm(text: str) -> str | None:
    """'7:5' / '07:05' -> '07:05'; None when it is not a time of day."""
    hh, _, mm = text.strip().partition(":")
    if hh.strip().isdigit() and mm.strip().isdigit() and int(hh) < 24 and int(mm) < 60:
        return f"{int(hh):02d}:{int(mm):02d}"
    return None


def combo(items: dict, value) -> QComboBox:
    c = QComboBox()
    for k, v in items.items():
        c.addItem(v, k)
    c.setCurrentIndex(max(0, c.findData(value)))
    return c


class SettingsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        header = PageHeader("الإعدادات", f"البيانات محفوظة على هذا الجهاز في: {data_dir()}")
        self.lay.addWidget(header)
        save = button("حفظ الإعدادات", "check", "primary")
        save.clicked.connect(self.save)
        header.add(save)
        self.tabs = QTabWidget()
        self.lay.addWidget(self.tabs, 1)
        self.w = {}
        self._build()

    # ------------------------------------------------------------ build
    def _tab(self, title: str) -> QFormLayout:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        form.setSpacing(12)
        lay.addLayout(form)
        lay.addStretch(1)
        self.tabs.addTab(w, title)
        w._outer = lay
        return form

    def _build(self):
        s = self.db.settings.all()
        W = self.w
        # general
        f = self._tab("عام")
        W["org.name"] = QLineEdit(s["org.name"])
        W["org.subtitle"] = QLineEdit(s["org.subtitle"])
        f.addRow("اسم الدائرة:", W["org.name"])
        f.addRow("الجهة/القسم:", W["org.subtitle"])
        logo_row = QHBoxLayout()
        self.logo_lbl = QLabel()
        self.logo_lbl.setFixedSize(90, 70)
        self.logo_lbl.setStyleSheet(f"background: {C['muted_bg']}; border-radius: 8px;")
        self._logo_b64 = s["org.logo"]
        choose = button("اختيار شعار…", "image")
        choose.clicked.connect(self._choose_logo)
        clear = button("إزالة", "x", "ghost")
        clear.clicked.connect(lambda: self._set_logo(""))
        logo_row.addWidget(self.logo_lbl)
        logo_row.addWidget(choose)
        logo_row.addWidget(clear)
        logo_row.addStretch(1)
        f.addRow("الشعار (يظهر في التقارير وشاشة الحضور):", logo_row)
        self._set_logo(self._logo_b64)
        W["ui.arabic_digits"] = combo({False: "أرقام لاتينية 0123", True: "أرقام عربية-هندية ٠١٢٣"},
                                      bool(s["ui.arabic_digits"]))
        W["ui.month_style"] = combo({"levant": "كانون الثاني، شباط…", "egypt": "يناير، فبراير…",
                                     "both": "كلاهما"}, s["ui.month_style"])
        W["ui.font_scale"] = QDoubleSpinBox()
        W["ui.font_scale"].setLocale(digit_locale())
        W["ui.font_scale"].setRange(0.8, 1.6)
        W["ui.font_scale"].setSingleStep(0.1)
        W["ui.font_scale"].setValue(float(s["ui.font_scale"]))
        f.addRow("شكل الأرقام:", W["ui.arabic_digits"])
        f.addRow("أسماء الأشهر:", W["ui.month_style"])
        f.addRow("حجم الخط:", W["ui.font_scale"])
        sig = s["reports.signatures"] or []
        self.sigs = [QLineEdit(sig[i] if i < len(sig) else "") for i in range(4)]
        srow = QHBoxLayout()
        for e in self.sigs:
            srow.addWidget(e)
        f.addRow("خانات التوقيع في التقارير:", srow)
        W["kiosk.start_in_kiosk"] = QCheckBox("يبدأ البرنامج بشاشة الحضور (موصى به لجهاز الباب)")
        W["kiosk.start_in_kiosk"].setChecked(bool(s["kiosk.start_in_kiosk"]))
        f.addRow("", W["kiosk.start_in_kiosk"])

        # camera
        f = self._tab("الكاميرا")
        W["camera.index"] = QComboBox()
        W["camera.index"].setEditable(True)
        for i in range(4):
            W["camera.index"].addItem(f"الكاميرا رقم {i}", i)
        W["camera.index"].setCurrentIndex(max(0, W["camera.index"].findData(int(s["camera.index"]))))
        find = button("بحث عن الكاميرات", "search")
        find.clicked.connect(self._find_cameras)
        crow = QHBoxLayout()
        crow.addWidget(W["camera.index"], 1)
        crow.addWidget(find)
        f.addRow("الكاميرا:", crow)
        W["camera.backend"] = combo({"auto": "تلقائي", "dshow": "DirectShow", "msmf": "Media Foundation",
                                     "any": "افتراضي النظام"}, s["camera.backend"])
        f.addRow("طريقة الاتصال:", W["camera.backend"])
        W["camera.res"] = combo({"1280x720": "1280×720 (موصى به)", "1920x1080": "1920×1080",
                                 "640x480": "640×480 (للأجهزة الضعيفة)"},
                                f"{s['camera.width']}x{s['camera.height']}")
        f.addRow("الدقة:", W["camera.res"])
        W["camera.mirror"] = QCheckBox("عرض الصورة كالمرآة")
        W["camera.mirror"].setChecked(bool(s["camera.mirror"]))
        f.addRow("", W["camera.mirror"])
        self.cam_view = CameraView(mirror=bool(s["camera.mirror"]), show_guide=False)
        self.cam_view.setMinimumHeight(300)
        self.tabs.widget(1)._outer.insertWidget(1, self.cam_view)
        self.cam_info = label("", "Hint")
        self.tabs.widget(1)._outer.insertWidget(2, self.cam_info)
        self.cam_timer = QTimer(self)
        self.cam_timer.timeout.connect(self._cam_frame)
        self._cam_seq = -1

        # recognition
        f = self._tab("التعرّف والأمان")
        W["recog.engine"] = combo(ENGINE_LABELS, s["recog.engine"])
        W["recog.security"] = combo(SECURITY_LABELS, s["recog.security"])
        W["recog.liveness"] = combo(LIVENESS_LABELS, s["recog.liveness"])
        W["recog.adaptive"] = QCheckBox("تحديث قوالب الوجه تلقائياً بعد التطابقات المؤكدة جداً"
                                        " (يتكيّف مع تغيّر اللحية والنظارة والعمر)")
        W["recog.adaptive"].setChecked(bool(s["recog.adaptive"]))
        W["recog.min_eye_px"] = SpinBox(20, 200, int(s["recog.min_eye_px"]), " بكسل")
        f.addRow("محرك التعرّف:", W["recog.engine"])
        f.addRow("مستوى التشدد:", W["recog.security"])
        f.addRow("كشف الصور والشاشات:", W["recog.liveness"])
        f.addRow("", W["recog.adaptive"])
        f.addRow("أقل مسافة بين العينين:", W["recog.min_eye_px"])
        f.addRow(label("النماذج المثبتة:", "CardTitle"))
        for spec in models.ALL_MODELS:
            ok = models.is_available(spec)
            lb = QLabel(("✔ " if ok else "✘ غير موجود — ") + f"{spec.title} — {spec.license}")
            lb.setStyleSheet(f"color: {C['ok'] if ok else C['bad']};")
            f.addRow("", lb)

        # kiosk
        f = self._tab("شاشة الحضور")
        W["kiosk.duplicate_minutes"] = SpinBox(0, 120, int(s["kiosk.duplicate_minutes"]), " دقيقة")
        f.addRow("تجاهل التسجيل المكرر خلال:", W["kiosk.duplicate_minutes"])
        W["kiosk.result_seconds"] = SpinBox(1, 15, int(s["kiosk.result_seconds"]), " ثانية")
        f.addRow("مدة عرض النتيجة:", W["kiosk.result_seconds"])
        for key, text in (("kiosk.show_recent", "عرض قائمة آخر التسجيلات"),
                          ("kiosk.show_names_recent", "إظهار الأسماء في قائمة آخر التسجيلات"),
                          ("kiosk.sound", "تشغيل صوت عند التسجيل"),
                          ("kiosk.fill_light", "إضاءة الشاشة تلقائياً عندما يكون المكان معتماً"),
                          ("kiosk.save_snapshots", "حفظ صورة صغيرة مع كل بصمة كدليل"),
                          ("kiosk.allow_pin", "السماح بالتسجيل بالرقم الوظيفي والرمز السري (احتياطي، يُعلَّم للمراجعة)")):
            W[key] = QCheckBox(text)
            W[key].setChecked(bool(s[key]))
            f.addRow("", W[key])
        W["kiosk.snapshot_days"] = SpinBox(0, 3650, int(s["kiosk.snapshot_days"]), " يوم")
        f.addRow("مدة الاحتفاظ بصور الأدلة (0 = دائماً):", W["kiosk.snapshot_days"])
        W["kiosk.admin_timeout_minutes"] = SpinBox(0, 120, int(s["kiosk.admin_timeout_minutes"]), " دقيقة")
        f.addRow("العودة لشاشة الحضور بعد خمول الإدارة:", W["kiosk.admin_timeout_minutes"])

        # rules
        f = self._tab("قواعد الدوام")
        self.rules_view = RulesOverview(self.db)
        self.tabs.widget(self.tabs.count() - 1)._outer.insertWidget(0, self.rules_view)
        W["attendance.day_start"] = QLineEdit(s["attendance.day_start"])
        W["attendance.day_start"].setInputMask("99:99")
        f.addRow("بداية اليوم (البصمات قبلها تُحسب لليوم السابق):", W["attendance.day_start"])
        W["attendance.dedupe_seconds"] = SpinBox(0, 900, int(s["attendance.dedupe_seconds"]), " ثانية")
        f.addRow("دمج البصمات المتقاربة خلال:", W["attendance.dedupe_seconds"])

        # telegram
        f = self._tab("تلغرام")
        outer = self.tabs.widget(self.tabs.count() - 1)._outer
        outer.insertWidget(0, label(
            "يرسل البرنامج إلى مجموعة أو قناة تلغرام رسالة عند كل تسجيل حضور أو انصراف (الاسم والصفة"
            " الوظيفية ووقت الدخول والخروج)، وقائمة يومية بأسماء الموظفين وحالاتهم. هذه الميزة وحدها تحتاج إلى"
            " إنترنت: إن انقطع الاتصال تُحفظ الرسائل وتُرسل عند عودته، ويبقى تسجيل الحضور يعمل كالمعتاد.",
            wrap=True))
        W["telegram.enabled"] = QCheckBox("تفعيل إشعارات تلغرام")
        W["telegram.enabled"].setChecked(bool(s["telegram.enabled"]))
        f.addRow("", W["telegram.enabled"])
        W["telegram.token"] = QLineEdit(s["telegram.token"])
        W["telegram.token"].setEchoMode(QLineEdit.Password)
        W["telegram.token"].setPlaceholderText("123456789:AAH…")
        W["telegram.token"].setLayoutDirection(Qt.LeftToRight)
        show = QCheckBox("إظهار")
        show.toggled.connect(lambda on: W["telegram.token"].setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        row = QHBoxLayout()
        row.addWidget(W["telegram.token"], 1)
        row.addWidget(show)
        f.addRow("رمز البوت (Bot Token):", row)
        W["telegram.chat_id"] = QLineEdit(s["telegram.chat_id"])
        W["telegram.chat_id"].setPlaceholderText("-1001234567890")
        W["telegram.chat_id"].setLayoutDirection(Qt.LeftToRight)
        find = button("بحث عن المجموعة", "search")
        find.clicked.connect(self._tg_find)
        row = QHBoxLayout()
        row.addWidget(W["telegram.chat_id"], 1)
        row.addWidget(find)
        f.addRow("معرّف المجموعة أو القناة:", row)
        W["telegram.punches"] = QCheckBox("رسالة عند كل تسجيل حضور أو انصراف")
        W["telegram.punches"].setChecked(bool(s["telegram.punches"]))
        f.addRow("", W["telegram.punches"])
        W["telegram.daily_list"] = QCheckBox("قائمة الحضور اليومية بالأسماء، الساعة")
        W["telegram.daily_list"].setChecked(bool(s["telegram.daily_list"]))
        W["telegram.daily_time"] = QLineEdit(s["telegram.daily_time"])
        W["telegram.daily_time"].setInputMask("99:99")
        W["telegram.daily_time"].setMaximumWidth(90)
        row = QHBoxLayout()
        row.addWidget(W["telegram.daily_list"])
        row.addWidget(W["telegram.daily_time"])
        row.addStretch(1)
        f.addRow("", row)
        self.tg_test = button("إرسال رسالة تجريبية", "send", "primary")
        self.tg_test.clicked.connect(self._tg_test)
        self.tg_list = button("إرسال قائمة اليوم الآن", "report")
        self.tg_list.clicked.connect(self._tg_send_list)
        row = QHBoxLayout()
        row.addWidget(self.tg_test)
        row.addWidget(self.tg_list)
        row.addStretch(1)
        f.addRow("", row)
        self.tg_status = label("", "Hint", wrap=True)
        f.addRow("الحالة:", self.tg_status)
        outer.insertWidget(2, label(
            "كيف تحصل على الرمز والمعرّف؟\n"
            "١. افتح في تلغرام الحساب ‎@BotFather وأرسل ‎/newbot واتبع الخطوات؛ سيعطيك «رمز البوت».\n"
            "٢. أضف البوت إلى مجموعتك، أو اجعله مشرفاً في القناة.\n"
            "٣. أرسل في المجموعة ‎/start ثم اضغط هنا «بحث عن المجموعة».\n"
            "٤. اضغط «إرسال رسالة تجريبية» للتأكد، ثم «حفظ الإعدادات».", "Hint", wrap=True))

        # backup
        f = self._tab("النسخ الاحتياطي")
        W["backup.dir"] = QLineEdit(s["backup.dir"])
        W["backup.dir"].setPlaceholderText(str(backups_dir()))
        pick = button("اختيار…", "download")
        pick.clicked.connect(self._pick_backup_dir)
        brow = QHBoxLayout()
        brow.addWidget(W["backup.dir"], 1)
        brow.addWidget(pick)
        f.addRow("مجلد النسخ (يُفضّل قرص خارجي):", brow)
        W["backup.auto"] = QCheckBox("نسخة احتياطية تلقائية يومياً")
        W["backup.auto"].setChecked(bool(s["backup.auto"]))
        f.addRow("", W["backup.auto"])
        W["backup.keep"] = SpinBox(1, 365, int(s["backup.keep"]), " نسخة")
        f.addRow("الاحتفاظ بآخر:", W["backup.keep"])
        W["backup.include_snapshots"] = QCheckBox("تضمين صور الأدلة في النسخة (حجم أكبر)")
        W["backup.include_snapshots"].setChecked(bool(s["backup.include_snapshots"]))
        f.addRow("", W["backup.include_snapshots"])
        self.last_backup = label("")
        f.addRow("آخر نسخة:", self.last_backup)
        b1 = button("نسخ احتياطي الآن", "download", "primary")
        b1.clicked.connect(self._backup_now)
        b2 = button("استرجاع نسخة…", "upload", "danger")
        b2.clicked.connect(self._restore)
        r = QHBoxLayout()
        r.addWidget(b1)
        r.addWidget(b2)
        r.addStretch(1)
        f.addRow("", r)

        # users
        w = QWidget()
        ul = QVBoxLayout(w)
        self.users = DataTable([("username", "اسم المستخدم", "text"), ("full_name", "الاسم", "text"),
                                ("role_ar", "الصلاحية", "text"), ("state", "الحالة", "text"),
                                ("last_login", "آخر دخول", "text")], stretch="full_name")
        ul.addWidget(self.users, 1)
        ur = QHBoxLayout()
        for text, ic, fn in (("مستخدم جديد", "user_plus", self._new_user), ("تعديل", "edit", self._edit_user),
                             ("تغيير كلمة المرور", "lock", self._password)):
            b = button(text, ic)
            b.clicked.connect(fn)
            ur.addWidget(b)
        ur.addStretch(1)
        ul.addLayout(ur)
        ul.addWidget(label("الصلاحيات: مدير النظام (كل شيء) — مشرف (الموظفون والإجازات والتصحيحات والتقارير)"
                           " — مطّلع (العرض والتقارير فقط).", "Hint", wrap=True))
        self.tabs.addTab(w, "المستخدمون")

        # about
        w = QWidget()
        al = QVBoxLayout(w)
        al.addWidget(label(f"{APP_TITLE} — {APP_SUBTITLE}", "PageTitle"))
        self.about_lbl = label("", wrap=True)
        al.addWidget(self.about_lbl)
        al.addWidget(label(f"قاعدة البيانات: {db_path()}\nصور الأدلة: {snapshots_dir()}", "Hint", wrap=True))
        al.addWidget(label("المكونات مفتوحة المصدر: Qt (LGPL)، OpenCV (Apache-2.0)، ONNX Runtime (MIT)،"
                           " نماذج YuNet (MIT)، SFace وArcFace (Apache-2.0)، MiniFASNet (Apache-2.0)،"
                           " خط Noto Sans Arabic (OFL). التفاصيل في ملف THIRD_PARTY_NOTICES.", "Hint", wrap=True))
        al.addStretch(1)
        self.tabs.addTab(w, "حول البرنامج")
        self.tabs.currentChanged.connect(self._tab_changed)

    # ------------------------------------------------------------ helpers
    def _set_logo(self, b64: str):
        self._logo_b64 = b64
        if b64:
            img = QImage.fromData(base64.b64decode(b64))
            self.logo_lbl.setPixmap(QPixmap.fromImage(img).scaled(88, 68, Qt.KeepAspectRatio,
                                                                  Qt.SmoothTransformation))
        else:
            self.logo_lbl.clear()

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "اختيار الشعار", "", "الصور (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            error(self, "تعذّرت قراءة الصورة.")
            return
        if img.height() > 300:
            img = img.scaledToHeight(300, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        self._set_logo(base64.b64encode(bytes(ba)).decode())

    def _find_cameras(self):
        cam = self.ctx.camera
        cam.stop()
        found = list_cameras(5, self.w["camera.backend"].currentData())
        cam.start()
        box = self.w["camera.index"]
        box.clear()
        for i in found or [0]:
            box.addItem(f"الكاميرا رقم {i}" + ("" if found else " (لم يُعثر على كاميرا)"), i)
        info(self, f"عُثر على {digits(len(found))} كاميرا." if found else "لم يُعثر على كاميرا متصلة.")

    def _tab_changed(self, i):
        if self.tabs.tabText(i) == "الكاميرا":
            self.cam_timer.start(33)
        else:
            self.cam_timer.stop()

    def _cam_frame(self):
        cam = self.ctx.camera
        frame, seq = cam.latest()
        self.cam_info.setText(f"الحالة: {'تعمل' if cam.state == 'ok' else cam.error or cam.state}"
                              f" — {digits(int(cam.fps))} إطار/ث — الإضاءة {digits(int(cam.brightness))}")
        if frame is not None and seq != self._cam_seq:
            self._cam_seq = seq
            self.cam_view.set_frame(frame)

    def _pick_backup_dir(self):
        d = QFileDialog.getExistingDirectory(self, "مجلد النسخ الاحتياطية")
        if d:
            self.w["backup.dir"].setText(d)

    def refresh(self):
        self.rules_view.reload()
        self._tg_status()
        privacy = ("إشعارات تلغرام مفعّلة: تُرسل أسماء الموظفين وصفاتهم وأوقات حضورهم إلى مجموعة تلغرام"
                   " المحددة، ولا يُرسل غير ذلك." if telegram.enabled(self.db) else
                   "لا تُرسل أي بيانات خارج هذا الجهاز.")
        self.about_lbl.setText(f"الإصدار {__version__} • يعمل دون إنترنت. {privacy}")
        last = self.db.settings.get("backup.last")
        self.last_backup.setText(digits(last[:16]) if last else "لم تُنشأ بعد")
        rows = []
        for u in self.db.users.list():
            rows.append(dict(u) | {"role_ar": ROLES.get(u["role"], u["role"]),
                                   "state": "فعّال" if u["active"] else "معطّل",
                                   "last_login": digits((u["last_login"] or "")[:16])})
        self.users.set_rows(rows)
        if self.tabs.tabText(self.tabs.currentIndex()) == "الكاميرا":
            self.cam_timer.start(33)

    def leave(self):
        self.cam_timer.stop()

    # -------------------------------------------------------------- save
    def save(self):
        W = self.w
        values = {}
        for key, w in W.items():
            if key == "camera.res":
                width, height = (int(x) for x in w.currentData().split("x"))
                values["camera.width"], values["camera.height"] = width, height
            elif key == "camera.index":
                data = w.currentData()
                values[key] = int(data) if data is not None else int("".join(ch for ch in w.currentText()
                                                                             if ch.isdigit()) or 0)
            elif isinstance(w, QCheckBox):
                values[key] = w.isChecked()
            elif isinstance(w, QComboBox):
                values[key] = w.currentData()
            elif isinstance(w, QDoubleSpinBox):
                values[key] = round(w.value(), 2)
            elif hasattr(w, "value"):
                values[key] = w.value()
            else:
                values[key] = w.text().strip()
        values["org.logo"] = self._logo_b64
        values["reports.signatures"] = [e.text().strip() for e in self.sigs if e.text().strip()]
        if not values["org.name"]:
            error(self, "اسم الدائرة مطلوب.")
            return
        day_start = valid_hhmm(values["attendance.day_start"])
        if day_start is None:
            error(self, "وقت «بداية اليوم» غير صحيح؛ اكتبه بصيغة ساعة:دقيقة مثل 04:00.")
            return
        values["attendance.day_start"] = day_start
        daily_time = valid_hhmm(values["telegram.daily_time"])
        if daily_time is None:
            error(self, "وقت قائمة الحضور اليومية غير صحيح؛ اكتبه مثل 17:05.")
            return
        values["telegram.daily_time"] = daily_time
        if values["telegram.enabled"] and not (values["telegram.token"] and values["telegram.chat_id"]):
            error(self, "لتفعيل إشعارات تلغرام أدخل رمز البوت ومعرّف المجموعة.")
            return
        old = self.db.settings.all()
        self.db.settings.set_many(values)
        self.ctx.reload_settings()
        Fmt.configure(values["ui.arabic_digits"], values["ui.month_style"])
        from PySide6.QtWidgets import QApplication

        if values["ui.font_scale"] != old["ui.font_scale"]:
            apply_theme(QApplication.instance(), values["ui.font_scale"])
        cam_keys = ("camera.index", "camera.backend", "camera.width", "camera.height")
        if any(values[k] != old[k] for k in cam_keys):
            cam = self.ctx.camera
            cam.stop()
            cam.index, cam.backend = values["camera.index"], values["camera.backend"]
            cam.width, cam.height = values["camera.width"], values["camera.height"]
            cam.start()
        self.ctx.worker.configure(self.ctx._settings)
        if self.ctx.telegram:
            self.ctx.telegram.wake()
        self.refresh()
        msg = "تم حفظ الإعدادات."
        if values["recog.engine"] != old["recog.engine"]:
            msg += "\nتغيير محرك التعرّف يتطلب إعادة تشغيل البرنامج."
        info(self, msg)

    # ---------------------------------------------------------- telegram
    def _tg_status(self):
        st = self.db.outbox.status()
        parts = [f"بانتظار الإرسال: {digits(st['pending'])}",
                 f"آخر إرسال: {digits(st['last_sent'][:16])}" if st["last_sent"] else "لم يُرسل شيء بعد"]
        if st["error"]:
            parts.append(f"آخر خطأ: {st['error']}")
        self.tg_status.setText(" • ".join(parts))

    def _tg_test(self):
        token = self.w["telegram.token"].text().strip()
        chat = self.w["telegram.chat_id"].text().strip()
        if not token or not chat:
            error(self, "أدخل رمز البوت ومعرّف المجموعة أولاً.")
            return
        text = (f"✅ رسالة تجريبية من برنامج «حاضر» — {self.w['org.name'].text().strip()}\n"
                "ستصل إلى هنا إشعارات الحضور والانصراف.")
        self.tg_test.setEnabled(False)
        run_in_background(self, lambda: telegram.send_message(token, chat, text), self._tg_test_done)

    def _tg_test_done(self, result):
        self.tg_test.setEnabled(True)
        if isinstance(result, Exception):
            error(self, f"لم تُرسل الرسالة:\n{result}")
        else:
            info(self, "وصلت الرسالة التجريبية إلى المجموعة. لا تنسَ «حفظ الإعدادات».")

    def _tg_find(self):
        token = self.w["telegram.token"].text().strip()
        if not token:
            error(self, "أدخل رمز البوت أولاً.")
            return
        run_in_background(self, lambda: telegram.find_chats(token), self._tg_found)

    def _tg_found(self, result):
        if isinstance(result, Exception):
            error(self, str(result))
            return
        if not result:
            info(self, "لم يُعثر على مجموعة بعد. أضف البوت إلى المجموعة وأرسل فيها ‎/start ثم أعد المحاولة.")
            return
        items = [f"{title} ({cid})" for cid, title in result]
        choice, ok = (items[0], True) if len(items) == 1 else QInputDialog.getItem(
            self, "اختيار المجموعة", "المجموعات التي وصل منها البوت:", items, 0, False)
        if not ok:
            return
        cid, title = result[items.index(choice)]
        self.w["telegram.chat_id"].setText(cid)
        info(self, f"اختيرت المجموعة «{title}». اضغط «إرسال رسالة تجريبية» للتأكد ثم «حفظ الإعدادات».")

    def _tg_send_list(self):
        if not telegram.enabled(self.db):
            error(self, "فعّل إشعارات تلغرام واحفظ الإعدادات أولاً.")
            return
        if not telegram.queue_daily_list(self.db, self.db.now().date()):
            info(self, "لا يوجد دوام اليوم، فلم تُرسل قائمة.")
            return
        if self.ctx.telegram:
            self.ctx.telegram.wake()
        self._tg_status()
        self.toast("أُضيفت قائمة اليوم إلى الإرسال", "ok")

    # ------------------------------------------------------------ backup
    def _backup_now(self):
        folder = self.w["backup.dir"].text().strip() or None
        try:
            path = maintenance.backup(self.db, folder, self.w["backup.include_snapshots"].isChecked())
        except OSError as exc:
            error(self, f"تعذّر إنشاء النسخة: {exc}")
            return
        self.refresh()
        info(self, f"تم إنشاء النسخة الاحتياطية:\n{path}")

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "اختيار نسخة احتياطية", str(backups_dir()),
                                              "نسخ حاضر (hadir-backup-*.zip);;Zip (*.zip)")
        if not path:
            return
        msg = maintenance.validate_backup(path)
        if msg:
            error(self, msg)
            return
        if not confirm(self, "سيتم استبدال جميع البيانات الحالية بمحتوى النسخة المختارة (مع الاحتفاظ بنسخة"
                             " من البيانات الحالية للاحتياط)، ثم يُغلق البرنامج لتعيد تشغيله. متابعة؟"):
            return
        self.db.events.add("restore", f"استرجاع نسخة: {path}")
        self.db.audit("استرجاع نسخة احتياطية", "backup", None, path)
        win = self.ctx.window
        self.ctx.worker.stop()
        self.ctx.camera.stop()
        self.db.close()
        try:
            maintenance.restore(path, db_path())
        except (ValueError, OSError) as exc:
            error(self, f"فشل الاسترجاع: {exc}")
        else:
            info(self, "تم الاسترجاع. أعد تشغيل البرنامج الآن.")
        win._allow_close = True
        win.close()
        sys.exit(0)

    # ------------------------------------------------------------- users
    def _new_user(self):
        dlg = UserDialog(self.db, None, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def _edit_user(self):
        r = self.users.selected()
        if r is None:
            error(self, "اختر مستخدماً.")
            return
        dlg = UserDialog(self.db, r, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()

    def _password(self):
        r = self.users.selected()
        uid = r["id"] if r else self.db.user.id
        dlg = QDialog(self)
        dlg.setWindowTitle("تغيير كلمة المرور")
        lay = QFormLayout(dlg)
        p1, p2 = QLineEdit(), QLineEdit()
        p1.setEchoMode(QLineEdit.Password)
        p2.setEchoMode(QLineEdit.Password)
        lay.addRow("كلمة المرور الجديدة:", p1)
        lay.addRow("تأكيدها:", p2)
        ok = QPushButton("حفظ")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(dlg.accept)
        lay.addRow("", ok)
        if dlg.exec() != QDialog.Accepted:
            return
        if p1.text() != p2.text():
            error(self, "كلمتا المرور غير متطابقتين.")
            return
        try:
            self.db.users.set_password(uid, p1.text())
        except StoreError as exc:
            error(self, str(exc))
            return
        info(self, "تم تغيير كلمة المرور.")


class UserDialog(QDialog):
    def __init__(self, db, row, parent=None):
        super().__init__(parent)
        self.db = db
        self.row = row
        self.setWindowTitle("مستخدم")
        lay = QFormLayout(self)
        self.username = QLineEdit(row["username"] if row else "")
        self.username.setEnabled(row is None)
        self.full_name = QLineEdit(row["full_name"] if row else "")
        self.role = combo(ROLES, row["role"] if row else "supervisor")
        self.active = QCheckBox("فعّال")
        self.active.setChecked(bool(row["active"]) if row else True)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        lay.addRow("اسم المستخدم:", self.username)
        lay.addRow("الاسم:", self.full_name)
        lay.addRow("الصلاحية:", self.role)
        lay.addRow("", self.active)
        if row is None:
            lay.addRow("كلمة المرور:", self.password)
        ok = QPushButton("حفظ")
        ok.setProperty("kind", "primary")
        ok.clicked.connect(self.save)
        lay.addRow("", ok)

    def save(self):
        try:
            if self.row is None:
                self.db.users.create(self.username.text(), self.full_name.text(), self.password.text(),
                                     self.role.currentData())
            else:
                self.db.users.update(self.row["id"], self.full_name.text(), self.role.currentData(),
                                     self.active.isChecked())
        except StoreError as exc:
            error(self, str(exc))
            return
        self.accept()

