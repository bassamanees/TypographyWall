"""
نافذة البدء: اختيار الكاميرا (لابتوب / ESP لاسلكية / فأرة) والاتصال المباشر بكاميرا ESP.

مبنية بـpygame ومُصيِّر النصوص العربي نفسه الذي يستعمله التطبيق (wall.arabic)، فتظهر العربية سليمة.
  - كاميرا اللابتوب: اختيار الكاميرا من القائمة.
  - كاميرا ESP: اتصال مباشر بشبكة الكاميرا نفسها (ESP32_CAM_NET) بزر واحد، ومؤشر حي للاتصال.
  - الفأرة: بلا كاميرا.
يعيد run_launcher() القرار {"use_camera", "camera", "fullscreen"} أو None إن أُغلقت النافذة.

ملاحظة: النصوص العربية تُكتب بلا كلمات لاتينية داخل الجملة؛ الأسماء والعناوين اللاتينية في أسطر مستقلة
(مُصيِّر النصوص يتعامل جيداً مع السطر العربي الصرف أو اللاتيني الصرف).
"""
import json
import os
import queue
import sys
import threading
import time

import pygame

from . import config, esp, usbcam
from .arabic import FontStack, TextRenderer
from .userdata import data_dir

W, H = 720, 736
M = 28
BG, PANEL, FIELD, LINE = (18, 17, 15), (28, 26, 23), (42, 39, 35), (66, 61, 54)
FG, DIM = (238, 232, 220), (163, 154, 140)
ACCENT, ACCENT_HI = (226, 120, 72), (240, 144, 96)
C_OK, C_WARN, C_ERR = (124, 207, 138), (232, 176, 74), (224, 97, 79)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCES = ("laptop", "usb", "esp", "mouse")
STABLE_CHECKS = 3     # فحوص ناجحة متتالية (نحو 6 ثوانٍ) قبل إظهار «متصل»
SOURCE_LABELS = {"laptop": "كاميرا اللابتوب", "usb": "ESP بالكابل", "esp": "ESP واي فاي",
                 "mouse": "الفأرة"}


# ----------------------------------------------------------------------
# التفضيلات (لا تُحفظ كلمة المرور أبداً)
# ----------------------------------------------------------------------
def _prefs_path():
    return os.path.join(data_dir(), "launcher.json")


def load_prefs():
    try:
        with open(_prefs_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(prefs):
    try:
        with open(_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def detect_laptop_cameras(max_index=4, stop=None):
    """أرقام كاميرات الحاسوب المتاحة. تتوقف بعد فشلين متتاليين (الأرقام متصلة عادة)."""
    try:
        import cv2
    except Exception:
        return [0]
    if sys.platform.startswith("linux"):
        backend = cv2.CAP_V4L2
    elif sys.platform == "darwin":
        backend = cv2.CAP_AVFOUNDATION
    else:
        backend = cv2.CAP_ANY
    found, fails = [], 0
    for i in range(max_index):
        if stop is not None and stop.is_set():
            break
        try:
            cap = cv2.VideoCapture(i, backend)
            ok = cap.isOpened()
            cap.release()
        except Exception:
            ok = False
        if ok:
            found.append(i)
            fails = 0
        else:
            fails += 1
            if fails >= 2:
                break
    return found


# ----------------------------------------------------------------------
class Launcher:
    def __init__(self, prefs, screen, ui=None, detect=True):
        self.prefs = prefs
        self.screen = screen
        self.ui = ui or TextRenderer(FontStack(config.FONT_CANDIDATES))
        self.result = None
        self.done = False
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.mouse = (0, 0)
        self.hits = []
        self.focus = None
        self._cache, self._wrap_cache = {}, {}
        self._blank = pygame.Surface((1, 1), pygame.SRCALPHA)

        src = prefs.get("source", "laptop")
        self.source = src if src in SOURCES else "laptop"
        self.cams = [int(prefs.get("laptop_index", 0))]
        self.cam_index = self.cams[0]
        self.fullscreen = bool(prefs.get("fullscreen", False))
        self.status = ("", "dim", "")
        self.cam_ok = None            # كاميرا ESP: هل الحاسوب على شبكتها؟ (None = لم نفحص بعد)
        self._joining = False
        self._streak = 0              # فحوص ناجحة متتالية: الأخضر فقط بعد STABLE_CHECKS (بعد أن يستقر ويندوز)
        self.usb_ports = []           # كاميرا ESP بالكابل: [(المنفذ، الشريحة)]
        self.usb_port = prefs.get("usb_port")

        if detect:
            threading.Thread(target=lambda: self.q.put(("cams", detect_laptop_cameras(stop=self.stop))),
                             daemon=True).start()
            threading.Thread(target=self._watch_camera, daemon=True).start()

    @staticmethod
    def _camera_link():
        """
        الحاسوب على شبكة الكاميرا بحسب النظام نفسه، والكاميرا ترد.
        كلاهما مطلوب: بعد الاتصال مباشرة قد ترد الكاميرا بينما ويندوز ما زال «يتعرّف على الشبكة»،
        ثم يقرر أنها «بلا إنترنت» فيقطعها أو يقفز لشبكة أخرى.
        """
        if sys.platform == "win32" and not esp.on_camera_wifi():
            return False
        return esp.camera_reachable(timeout=1.0)

    def _watch_camera(self):
        """يفحص كل ثانيتين هل الحاسوب متصل بشبكة الكاميرا (فقط حين يكون تبويب ESP مفتوحاً)."""
        while not self.stop.is_set():
            if self.source == "esp" and not self._joining:
                self.q.put(("reach", self._camera_link()))
            elif self.source == "usb":
                self.q.put(("ports", usbcam.find_ports()))
            self.stop.wait(2.0)

    # ------------------------------------------------------------------ نص
    def text(self, s, size=17, color=FG, weight=400):
        if not s:
            return self._blank
        key = (s, size, color, weight)
        surf = self._cache.get(key)
        if surf is None:
            if len(self._cache) > 500:
                self._cache.clear()
            try:
                surf = self.ui.render(s, size, weight, color)
            except Exception:
                surf = self._blank
            self._cache[key] = surf
        return surf

    def width(self, s, size=17, weight=400):
        return self.text(s, size, FG, weight).get_width()

    def wrap(self, s, size, maxw):
        key = (s, size, maxw)
        lines = self._wrap_cache.get(key)
        if lines is None:
            lines = []
            for para in s.split("\n"):
                cur = ""
                for word in para.split(" "):
                    cand = f"{cur} {word}" if cur else word
                    if cur and self.width(cand, size) > maxw:
                        lines.append(cur)
                        cur = word
                    else:
                        cur = cand
                lines.append(cur)
            if len(self._wrap_cache) > 100:
                self._wrap_cache.clear()
            self._wrap_cache[key] = lines
        return lines

    def put_r(self, s, right, y, size=17, color=FG, weight=400):
        surf = self.text(s, size, color, weight)
        self.screen.blit(surf, (right - surf.get_width(), y))
        return surf

    def put_c(self, s, rect, size=17, color=FG, weight=400):
        surf = self.text(s, size, color, weight)
        self.screen.blit(surf, surf.get_rect(center=rect.center))

    # ------------------------------------------------------------------ عناصر (رسم فوري + مناطق نقر)
    def hit(self, rect, fn):
        self.hits.append((rect, fn))

    def button(self, rect, label, fn, primary=False, enabled=True, size=17):
        hover = enabled and rect.collidepoint(self.mouse)
        if not enabled:
            col, fg = (40, 38, 34), (110, 104, 94)
        elif primary:
            col, fg = (ACCENT_HI if hover else ACCENT), (20, 18, 15)
        else:
            col, fg = ((76, 71, 63) if hover else (56, 52, 46)), FG
        pygame.draw.rect(self.screen, col, rect, border_radius=10)
        self.put_c(label, rect, size, fg, 600 if primary else 500)
        if enabled:
            self.hit(rect, fn)

    def chip(self, right, y, label, selected, fn, size=15):
        w = self.width(label, size) + 26
        rect = pygame.Rect(right - w, y, w, 30)
        hover = rect.collidepoint(self.mouse)
        col = ACCENT if selected else ((76, 71, 63) if hover else (52, 48, 42))
        pygame.draw.rect(self.screen, col, rect, border_radius=15)
        self.put_c(label, rect, size, (20, 18, 15) if selected else FG)
        self.hit(rect, fn)
        return rect

    def checkbox(self, right, y, label, checked, fn):
        w = self.width(label, 16)
        box = pygame.Rect(right - w - 34, y, 24, 24)
        rect = pygame.Rect(box.left - 6, y - 4, w + 46, 32)
        pygame.draw.rect(self.screen, FIELD, box, border_radius=6)
        pygame.draw.rect(self.screen, ACCENT if checked else LINE, box, 2, border_radius=6)
        if checked:
            pygame.draw.lines(self.screen, ACCENT, False,
                              [(box.x + 5, box.centery), (box.x + 10, box.bottom - 7), (box.right - 5, box.y + 6)], 3)
        self.put_r(label, right, y - 1, 16, FG)
        self.hit(rect, fn)

    # ------------------------------------------------------------------ رسم
    def draw(self):
        s = self.screen
        s.fill(BG)
        self.hits = []
        R = W - M
        self.put_r("جدار التيبوغرافيا التوليدية التفاعلية", R, 22, 28, FG, 600)
        self.put_r("اختر مصدر الكاميرا ثم اضغط «ابدأ»", R, 66, 16, DIM)

        # تبويبات المصدر
        gap = 10
        tw = (W - 2 * M - gap * (len(SOURCES) - 1)) // len(SOURCES)
        for i, name in enumerate(SOURCES):
            rect = pygame.Rect(R - (i + 1) * tw - i * gap, 104, tw, 48)
            sel = self.source == name
            hover = rect.collidepoint(self.mouse)
            pygame.draw.rect(s, ACCENT if sel else ((52, 48, 42) if hover else PANEL), rect, border_radius=12)
            self.put_c(SOURCE_LABELS[name], rect, 17, (20, 18, 15) if sel else FG, 600 if sel else 500)
            self.hit(rect, lambda n=name: self.select(n))

        panel = pygame.Rect(M, 164, W - 2 * M, 372 if self.source == "esp" else 300 if self.source == "usb" else 120)
        pygame.draw.rect(s, PANEL, panel, border_radius=14)
        {"laptop": self.draw_laptop, "usb": self.draw_usb, "esp": self.draw_esp, "mouse": self.draw_mouse}[self.source](panel)

        self.draw_status(R, 548)
        self.checkbox(R, 622, "ملء الشاشة", self.fullscreen, lambda: setattr(self, "fullscreen", not self.fullscreen))
        self.button(pygame.Rect(M, 660, W - 2 * M, 54), "ابدأ", self.start, primary=True, size=22)

    def draw_laptop(self, panel):
        R = panel.right - 18
        self.put_r("اختر الكاميرا", R, panel.y + 16, 17, FG, 600)
        x = R
        for i in self.cams:
            x = self.chip(x, panel.y + 52, f"الكاميرا {i}", i == self.cam_index,
                          lambda i=i: setattr(self, "cam_index", i)).left - 8
        self.put_r("الكاميرا المدمجة في اللابتوب أو كاميرا USB", R, panel.y + 90, 14, DIM)

    def draw_usb(self, panel):
        """كاميرا ESP بكابل USB: بلا واي فاي، المنفذ يُكتشف وحده."""
        s = self.screen
        L, R = panel.x + 18, panel.right - 18
        y = panel.y + 16
        self.put_r("كاميرا ESP بكابل USB", R, y, 18, FG, 600)
        self.put_r("بلا واي فاي: الصورة تمر عبر الكابل مباشرة", R, y + 28, 14, DIM)
        y += 66
        self.put_r("١.  صِل الكاميرا بالحاسوب بالكابل", R, y, 16, FG)
        y += 34
        self.put_r("٢.  أغلق برنامج Arduino إن كان مفتوحاً", R, y, 16, FG)
        y += 34
        self.put_r("٣.  حين تظهر العلامة الخضراء اضغط «ابدأ»", R, y, 16, FG)
        y += 42
        ok = bool(self.usb_ports)
        ind = pygame.Rect(L, y, R - L, 50)
        pygame.draw.rect(s, (30, 52, 36) if ok else (52, 42, 26), ind, border_radius=10)
        pygame.draw.circle(s, C_OK if ok else C_WARN, (ind.right - 26, ind.centery), 8)
        surf = self.text("الكاميرا موصولة بالكابل" if ok else "لم أجد الكاميرا على أي منفذ بعد",
                         17, C_OK if ok else C_WARN, 600)
        s.blit(surf, surf.get_rect(midright=(ind.right - 46, ind.centery)))
        # المنافذ على اليسار (اختيار إن وُجد أكثر من واحد)
        x = ind.left + 12
        for dev, desc in self.usb_ports[:3]:
            label = f"{dev} · {desc}"
            w = self.width(label, 14) + 24
            rect = pygame.Rect(x, ind.y + 10, w, 30)
            sel = dev == self.usb_port
            pygame.draw.rect(s, ACCENT if sel else (52, 48, 42), rect, border_radius=15)
            self.put_c(label, rect, 14, (20, 18, 15) if sel else FG)
            self.hit(rect, lambda d=dev: setattr(self, "usb_port", d))
            x += w + 8

    def draw_mouse(self, panel):
        R = panel.right - 18
        self.put_r("بدون كاميرا", R, panel.y + 16, 17, FG, 600)
        self.put_r("حرّك الفأرة لتمثّل اليد، والزر الأيسر للإمساك، والعجلة للاقتراب", R, panel.y + 52, 14, DIM)

    def draw_esp(self, panel):
        """اتصال مباشر: الحاسوب يتصل بشبكة الكاميرا نفسها، بلا راوتر ولا إدخال شبكة أخرى."""
        s = self.screen
        L, R = panel.x + 18, panel.right - 18
        y = panel.y + 16
        self.put_r("اتصال مباشر بالكاميرا", R, y, 18, FG, 600)
        self.put_r("الحاسوب يتصل بشبكة الكاميرا نفسها، بلا إنترنت ولا إعداد", R, y + 28, 14, DIM)
        y += 66
        self.put_r("١.  شغّل الكاميرا بالباور وانتظر نحو عشرين ثانية", R, y, 16, FG)
        y += 36
        self.put_r("٢.  صِل الحاسوب بشبكة الكاميرا:", R, y, 16, FG)
        y += 36
        # اسم الشبكة وكلمتها في سطر لاتيني مستقل وبخط أكبر
        box = pygame.Rect(L + 220, y, R - (L + 220), 44)
        pygame.draw.rect(s, FIELD, box, border_radius=8)
        self.put_c(f"{esp.AP_SSID}    ·    {esp.AP_PASSWORD}", box, 17, FG, 600)
        self.button(pygame.Rect(L, y, 206, 44), "جارٍ الاتصال…" if self._joining else "اتصل تلقائياً",
                    self.join, enabled=not (self._joining or self.cam_ok), size=16)
        y += 60
        self.put_r("٣.  حين تظهر العلامة الخضراء اضغط «ابدأ»", R, y, 16, FG)
        y += 40
        # مؤشر حي للاتصال: أخضر فقط بعد أن يستقر
        ok = bool(self.cam_ok)
        settling = not ok and (self._streak > 0 or self._joining)
        ind = pygame.Rect(L, y, R - L, 50)
        pygame.draw.rect(s, (30, 52, 36) if ok else (52, 42, 26), ind, border_radius=10)
        pygame.draw.circle(s, C_OK if ok else C_WARN, (ind.right - 26, ind.centery), 8)
        label = ("الحاسوب متصل بالكاميرا" if ok else
                 "أثبّت الاتصال… انتظر لحظات" if settling else
                 "أفحص الاتصال…" if self.cam_ok is None else "الحاسوب ليس على شبكة الكاميرا بعد")
        surf = self.text(label, 17, C_OK if ok else C_WARN, 600)
        s.blit(surf, surf.get_rect(midright=(ind.right - 46, ind.centery)))
        y += 62
        self.put_r("ما دام الحاسوب على شبكة الكاميرا ينقطع عنه الإنترنت، وهذا طبيعي.", R, y, 14, DIM)

    def draw_status(self, right, y):
        text, kind, detail = self.status
        color = {"ok": C_OK, "warn": C_WARN, "err": C_ERR}.get(kind, DIM)
        lines = self.wrap(text, 16, W - 2 * M)[:3] if text else []
        for i, ln in enumerate(lines):
            self.put_r(ln, right, y + i * 22, 16, color)
        if detail and len(lines) < 3:
            self.put_r(detail, right, y + len(lines) * 22, 15, color)

    def set_status(self, text, kind="dim", detail=""):
        self.status = (text, kind, detail)

    # ------------------------------------------------------------------ أفعال
    def select(self, name):
        self.source = name
        self.focus = None
        self.set_status("")
        if name == "laptop" and len(self.cams) == 0:
            self.set_status("لم أجد كاميرا في هذا الحاسوب — جرّب كاميرا ESP أو الفأرة.", "warn")

    def join(self):
        """زر «اتصل تلقائياً»: يوصل الحاسوب بشبكة الكاميرا بأمر النظام."""
        if self._joining:
            return
        self._joining = True
        self.set_status("أوصل الحاسوب بشبكة الكاميرا… (قد يستغرق بضع ثوانٍ)", "dim")

        def work():
            ok, msg = esp.join_camera_wifi()
            # ننتظر حتى يستقر الاتصال: STABLE_CHECKS فحوص ناجحة متتالية (ويندوز ينهي «التعرّف على الشبكة»)
            streak, t0 = 0, time.monotonic()
            while time.monotonic() - t0 < 30 and streak < STABLE_CHECKS:
                if self.stop.is_set():
                    return
                streak = streak + 1 if self._camera_link() else 0
                self.q.put(("streak", streak))
                self.stop.wait(1.5)
            self.q.put(("joined", ok, streak >= STABLE_CHECKS, msg))

        threading.Thread(target=work, daemon=True).start()

    def start(self):
        src = self.source
        if src == "esp" and not self.cam_ok and self._streak:
            self.set_status("انتظر لحظات حتى يستقر الاتصال بالكاميرا، ثم اضغط «ابدأ».", "warn")
            return
        if src == "esp" and not self.cam_ok:
            self.set_status("الحاسوب ليس على شبكة الكاميرا. شغّل الكاميرا ثم اضغط «اتصل تلقائياً»، "
                            "أو اختر شبكتها يدوياً من قائمة الواي فاي.", "err")
            return
        self.prefs.update(source=src, fullscreen=self.fullscreen)
        if src == "laptop":
            self.prefs["laptop_index"] = self.cam_index
            self.result = {"use_camera": True, "camera": self.cam_index}
        elif src == "usb":
            if not self.usb_port:
                self.set_status("صِل الكاميرا بالكابل أولاً وانتظر ظهور العلامة الخضراء.", "err")
                return
            self.prefs["usb_port"] = self.usb_port
            self.result = {"use_camera": True, "camera": usbcam.PREFIX + self.usb_port}
        elif src == "esp":
            self.result = {"use_camera": True, "camera": esp.DIRECT_STREAM}
        else:
            self.result = {"use_camera": False, "camera": 0}
        self.result["fullscreen"] = self.fullscreen
        save_prefs(self.prefs)
        self._finish()

    def on_close(self):
        self.result = None
        self._finish()

    def _finish(self):
        self.stop.set()
        self.done = True

    # ------------------------------------------------------------------ أحداث
    def handle(self, ev):
        t = ev.type
        if t == pygame.QUIT:
            self.on_close()
        elif t == pygame.MOUSEMOTION:
            self.mouse = ev.pos
        elif t == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self.mouse = ev.pos
            self.focus = None if not any(r.collidepoint(ev.pos) for r, _ in self.hits) else self.focus
            for rect, fn in reversed(self.hits):
                if rect.collidepoint(ev.pos):
                    fn()
                    break
        elif t == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                self.on_close()
            elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.start()

    # ------------------------------------------------------------------ خيوط → واجهة
    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "cams":
                    self.cams = msg[1] or [0]
                    if self.cam_index not in self.cams:
                        self.cam_index = self.cams[0]
                    if not msg[1] and self.source == "laptop":
                        self.set_status("لم أجد كاميرا في هذا الحاسوب — جرّب كاميرا ESP أو الفأرة.", "warn")
                elif kind == "ports":
                    self.usb_ports = msg[1]
                    devs = [d for d, _ in msg[1]]
                    if self.usb_port not in devs:
                        self.usb_port = devs[0] if devs else None
                elif kind == "streak":
                    self._streak = msg[1]
                elif kind == "reach":
                    if self._joining:
                        continue
                    was = self.cam_ok
                    self._streak = self._streak + 1 if msg[1] else 0
                    self.cam_ok = self._streak >= STABLE_CHECKS if msg[1] else False
                    if self.source == "esp" and was is not True and self.cam_ok:
                        self.set_status("الكاميرا جاهزة والاتصال مستقر. اضغط «ابدأ».", "ok")
                    elif self.source == "esp" and was is True and not self.cam_ok:
                        self.set_status("انقطع الاتصال بشبكة الكاميرا (ربما قفز ويندوز إلى شبكة فيها إنترنت). "
                                        "اضغط «اتصل تلقائياً» مرة أخرى.", "warn")
                elif kind == "joined":
                    _, ok, reach, text = msg
                    self._joining = False
                    self.cam_ok = reach
                    self._streak = STABLE_CHECKS if reach else 0
                    if reach:
                        self.set_status("تم الاتصال بالكاميرا واستقر. اضغط «ابدأ».", "ok")
                    elif ok:
                        self.set_status("الاتصال لم يستقر: ويندوز ترك شبكة الكاميرا أو الكاميرا لم ترد. تأكد أنها تعمل واضغط «اتصل تلقائياً» مرة أخرى.",
                                        "warn")
                    else:
                        self.set_status("تعذّر الاتصال تلقائياً. هل الكاميرا تعمل؟ يمكنك أيضاً اختيار شبكتها "
                                        "يدوياً من قائمة الواي فاي في الحاسوب.", "err",
                                        (text or "").splitlines()[-1][:90] if text else "")
        except queue.Empty:
            pass

    def run(self):
        clock = pygame.time.Clock()
        while not self.done:
            for ev in pygame.event.get():
                self.handle(ev)
            self.poll()
            self.draw()
            pygame.display.flip()
            clock.tick(30)


def run_launcher():
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    pygame.init()
    try:
        screen = pygame.display.set_mode((W, H))
        pygame.display.set_caption("جدار التيبوغرافيا التوليدية التفاعلية")
        try:
            icon = os.path.join(ROOT, "app_icon.png")
            if os.path.isfile(icon):
                pygame.display.set_icon(pygame.image.load(icon))
        except Exception:
            pass
        try:
            pygame.key.start_text_input()
        except Exception:
            pass
        pygame.key.set_repeat(350, 40)
        ui = Launcher(load_prefs(), screen)
        ui.run()
        return ui.result
    finally:
        pygame.quit()
