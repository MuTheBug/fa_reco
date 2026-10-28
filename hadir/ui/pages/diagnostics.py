"""Recognition test screen: live scores, quality, liveness, look-alike report."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel

from ...core.settings import SECURITY_LEVELS
from ...core.timeutil import digits
from ...face.pipeline import Status
from ..admin import Page
from ..theme import C
from ..widgets.camera_view import CameraView
from ..widgets.common import Card, DataTable, PageHeader, button, label


class DiagnosticsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.lay.addWidget(PageHeader("اختبار التعرّف", "قِف أمام الكاميرا لرؤية درجات المطابقة والجودة والحيوية"
                                      " كما يراها النظام — مفيد لضبط مكان الجهاز وإضاءته"))
        body = QHBoxLayout()
        self.view = CameraView(mirror=bool(ctx.setting("camera.mirror")))
        self.view.setMinimumHeight(420)
        body.addWidget(self.view, 3)
        side = Card("القراءات الحية")
        form = QFormLayout()
        self.vals = {}
        for key, title in (("faces", "عدد الوجوه"), ("size", "المسافة بين العينين"), ("pose", "اتجاه الرأس"),
                           ("light", "الإضاءة"), ("sharp", "الوضوح"), ("quality", "الحكم على الجودة"),
                           ("live", "درجة الحيوية"), ("ms", "زمن المعالجة")):
            lb = QLabel("—")
            self.vals[key] = lb
            form.addRow(title + ":", lb)
        side.lay.addLayout(form)
        side.lay.addWidget(label("أقرب الموظفين:", "CardTitle"))
        self.cands = DataTable([("name", "الموظف", "text"), ("primary", "المحرك الرئيسي", "float"),
                                ("secondary", "المحرك المساند", "float")], stretch="name")
        self.cands.setMaximumHeight(160)
        side.lay.addWidget(self.cands)
        self.thr = label("", "Hint", wrap=True)
        side.lay.addWidget(self.thr)
        body.addWidget(side, 2)
        self.lay.addLayout(body)

        sim = Card("تقرير التشابه بين الموظفين المسجلين")
        sim.lay.addWidget(label("لكل موظف: أكثر موظف آخر يشبهه ودرجة التشابه. الدرجات المرتفعة (مثل الإخوة"
                                " والتوائم) تعني ضرورة إضافة عينات أكثر لكل منهما؛ ويرفض النظام التسجيل"
                                " عند الالتباس بدلاً من الخطأ.", "Hint", wrap=True))
        self.sim = DataTable([("name", "الموظف", "text"), ("other", "الأشبه به", "text"),
                              ("score", "التشابه", "float"), ("risk", "التقييم", "text")], stretch="name")
        self.sim.setMinimumHeight(200)
        sim.lay.addWidget(self.sim)
        b = button("تحديث التقرير", "refresh")
        b.clicked.connect(self._similarity)
        sim.lay.addWidget(b)
        self.lay.addWidget(sim)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._frame)
        self._seq = -1
        self._names = {}
        self._connected = False

    def refresh(self):
        w = self.ctx.worker
        self._names = self.db.employees.names()
        lvl = SECURITY_LEVELS.get(self.ctx.setting("recog.security"), SECURITY_LEVELS["high"])
        if not w.ready_ok:
            self.view.placeholder = "نظام التعرّف لم يكتمل تحميله بعد…"
            return
        prim, sec = w.engine.primary.id, (w.engine.secondary.id if w.engine.secondary else "—")
        self.thr.setText(f"المحرك الرئيسي: {prim} — المساند: {sec}. حد القبول الحالي {lvl['arc']:.2f}"
                         f" وفارق لا يقل عن {lvl['margin']:.2f} عن أقرب موظف آخر.")
        if not self._connected:
            w.analysis.connect(self._on_analysis)
            self._connected = True
        w.set_mode("diagnose")
        self.timer.start(33)
        self._similarity()

    def leave(self):
        self.timer.stop()
        if self._connected:
            self.ctx.worker.analysis.disconnect(self._on_analysis)
            self._connected = False
        self.ctx.worker.set_mode("idle")

    def _frame(self):
        frame, seq = self.ctx.camera.latest()
        if frame is not None and seq != self._seq:
            self._seq = seq
            self.view.set_frame(frame)

    def _on_analysis(self, a, frame):
        st = Status(state="scanning", box=a.face.box if a.face else None, tone="busy")
        q = a.quality
        self.vals["faces"].setText(digits(len(a.faces)))
        self.vals["ms"].setText(f"{digits(int(a.ms))} مللي ثانية")
        if q is None:
            for k in ("size", "pose", "light", "sharp", "quality", "live"):
                self.vals[k].setText("—")
            self.cands.set_rows([])
            st.state = "idle"
            self.view.set_status(st)
            return
        self.vals["size"].setText(f"{digits(int(q.eye_px))} بكسل")
        self.vals["pose"].setText(f"يمين/يسار {q.yaw:+.2f} — أعلى/أسفل {q.pitch:.2f}")
        self.vals["light"].setText(f"{digits(int(q.brightness))} (التباين {digits(int(q.contrast))})")
        self.vals["sharp"].setText(digits(int(q.sharpness)))
        self.vals["quality"].setText("جيدة ✔" if q.ok else q.reason)
        self.vals["quality"].setStyleSheet(f"color: {C['ok'] if q.ok else C['warn']}; font-weight: bold;")
        self.vals["live"].setText("—" if a.liveness is None else f"{a.liveness:.2f}")
        w = self.ctx.worker
        prim = a.scores.get(w.engine.primary.id, {})
        sec = a.scores.get(w.engine.secondary.id, {}) if w.engine.secondary else {}
        top = sorted(prim.items(), key=lambda kv: -kv[1])[:3]
        self.cands.set_rows([{"name": self._names.get(e, e), "primary": s, "secondary": sec.get(e)}
                             for e, s in top])
        st.tone = "ok" if q.ok else "warn"
        st.message = "" if q.ok else q.reason
        self.view.set_status(st)

    def _similarity(self):
        w = self.ctx.worker
        if not w.ready_ok:
            return
        g = w.engine.galleries.get(w.engine.primary.id)
        if g is None or g.empty:
            self.sim.set_rows([])
            return
        names = self.db.employees.names()
        rows = []
        thr = SECURITY_LEVELS["high"]["arc"]
        for emp, other, score in sorted(g.cross_report(), key=lambda x: -x[2]):
            risk = "مرتفع: أضف عينات" if score >= thr else ("متوسط" if score >= thr - 0.15 else "سليم")
            rows.append({"name": names.get(emp, emp), "other": names.get(other, other), "score": score,
                         "risk": risk, "_tone": "bad" if score >= thr else ("warn" if score >= thr - 0.15 else "")})
        self.sim.set_rows(rows)
