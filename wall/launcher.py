"""
نافذة البدء: اختيار الكاميرا (لابتوب / ESP لاسلكية / فأرة) وإعداد شبكة كاميرا ESP دون أي تعقيد.

مبنية بـpygame ومُصيِّر النصوص العربي نفسه الذي يستعمله التطبيق (wall.arabic)، فتظهر العربية سليمة.
  - كاميرا اللابتوب: اختيار الكاميرا من القائمة.
  - كاميرا ESP: بحث تلقائي على الشبكة، ثم خانتا اسم الشبكة وكلمتها وزر «إرسال» يحفظهما في الكاميرا.
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
import urllib.parse

import pygame

from . import config, esp
from .arabic import FontStack, TextRenderer
from .userdata import data_dir

W, H = 720, 736
M = 28
BG, PANEL, FIELD, LINE = (18, 17, 15), (28, 26, 23), (42, 39, 35), (66, 61, 54)
FG, DIM = (238, 232, 220), (163, 154, 140)
ACCENT, ACCENT_HI = (226, 120, 72), (240, 144, 96)
C_OK, C_WARN, C_ERR = (124, 207, 138), (232, 176, 74), (224, 97, 79)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCES = ("laptop", "esp", "mouse")
SOURCE_LABELS = {"laptop": "كاميرا اللابتوب", "esp": "كاميرا ESP", "mouse": "الفأرة"}


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


def host_of(text):
    """العنوان فقط من نص كتبه المستخدم (يقبل رابطاً كاملاً)."""
    url = esp.normalize_source(text)
    return urllib.parse.urlparse(url).hostname if url else None


def has_arabic(s):
    return any("؀" <= c <= "ۿ" for c in s)


def read_clipboard():
    """نص الحافظة (للصق اسم الشبكة/كلمتها). لا شيء إن تعذّر."""
    try:
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        try:
            return r.clipboard_get()
        finally:
            r.destroy()
    except Exception:
        pass
    try:
        pygame.scrap.init()
        data = pygame.scrap.get(pygame.SCRAP_TEXT)
        if data:
            return data.decode("utf-8", "ignore").strip("\x00")
    except Exception:
        pass
    return ""


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
        self.vals = {"address": prefs.get("esp_host", "") or "", "ssid": prefs.get("esp_ssid", "") or "", "pass": ""}
        self.show_pw = False
        self.fullscreen = bool(prefs.get("fullscreen", False))
        self.status = ("", "dim", "")
        self.nets = []
        self.cam_info = None
        self._searching = self._waiting = self._sending = False
        self._search_id = 0
        self._search_progress = ""

        if detect:
            threading.Thread(target=lambda: self.q.put(("cams", detect_laptop_cameras(stop=self.stop))),
                             daemon=True).start()
        if self.source == "esp":
            self.search()

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

    def field(self, key, rect, placeholder="", password=False):
        focused = self.focus == key
        pygame.draw.rect(self.screen, FIELD, rect, border_radius=8)
        pygame.draw.rect(self.screen, ACCENT if focused else LINE, rect, 2 if focused else 1, border_radius=8)
        val = self.vals[key]
        inner = rect.inflate(-20, 0)
        self.screen.set_clip(inner)
        if val:
            shown = "•" * len(val) if (password and not self.show_pw) else val
            surf = self.text(shown, 18, FG)
            rtl = has_arabic(shown)
            if rtl:
                x = inner.right - surf.get_width() if surf.get_width() <= inner.width else inner.left
                caret_x = x - 3
            else:
                x = inner.left if surf.get_width() <= inner.width else inner.right - surf.get_width()
                caret_x = x + surf.get_width() + 3
            self.screen.blit(surf, (x, rect.centery - surf.get_height() // 2))
        else:
            if placeholder:
                ph = self.text(placeholder, 16, (110, 104, 94))
                self.screen.blit(ph, (inner.right - ph.get_width(), rect.centery - ph.get_height() // 2))
            caret_x = inner.left + 2
        if focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            pygame.draw.line(self.screen, ACCENT, (caret_x, rect.y + 10), (caret_x, rect.bottom - 10), 2)
        self.screen.set_clip(None)
        self.hit(rect, lambda: setattr(self, "focus", key))

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
        gap, tw = 10, (W - 2 * M - 20) // 3
        for i, name in enumerate(SOURCES):
            rect = pygame.Rect(R - (i + 1) * tw - i * gap, 104, tw, 48)
            sel = self.source == name
            hover = rect.collidepoint(self.mouse)
            pygame.draw.rect(s, ACCENT if sel else ((52, 48, 42) if hover else PANEL), rect, border_radius=12)
            self.put_c(SOURCE_LABELS[name], rect, 18, (20, 18, 15) if sel else FG, 600 if sel else 500)
            self.hit(rect, lambda n=name: self.select(n))

        panel = pygame.Rect(M, 164, W - 2 * M, 372 if self.source == "esp" else 120)
        pygame.draw.rect(s, PANEL, panel, border_radius=14)
        {"laptop": self.draw_laptop, "esp": self.draw_esp, "mouse": self.draw_mouse}[self.source](panel)

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

    def draw_mouse(self, panel):
        R = panel.right - 18
        self.put_r("بدون كاميرا", R, panel.y + 16, 17, FG, 600)
        self.put_r("حرّك الفأرة لتمثّل اليد، والزر الأيسر للإمساك، والعجلة للاقتراب", R, panel.y + 52, 14, DIM)

    def draw_esp(self, panel):
        s = self.screen
        L, R = panel.x + 18, panel.right - 18
        y = panel.y + 16
        # العنوان + زر البحث
        self.put_r("العنوان", R, y + 11, 15, DIM)
        self.button(pygame.Rect(L, y, 118, 44), "جارٍ البحث…" if self._searching else "بحث",
                    self.search, enabled=not self._searching, size=16)
        self.field("address", pygame.Rect(L + 128, y, R - 70 - (L + 128), 44), "192.168.1.50")
        y += 56
        pygame.draw.line(s, LINE, (L, y - 6), (R, y - 6))
        self.put_r("شبكة الواي فاي التي ستتصل بها الكاميرا", R, y, 17, FG, 600)
        y += 30
        # اسم الشبكة
        self.put_r("الاسم", R, y + 11, 15, DIM)
        self.field("ssid", pygame.Rect(L, y, R - 70 - L, 44), "اسم الشبكة")
        y += 50
        # شبكات تراها الكاميرا
        if self.nets:
            x = R
            for name in self.nets[:6]:
                label = name if len(name) <= 20 else name[:19] + "…"
                if x - (self.width(label, 15) + 26) < L:
                    break
                x = self.chip(x, y, label, name == self.vals["ssid"].strip(),
                              lambda n=name: self.vals.__setitem__("ssid", n)).left - 8
        else:
            self.put_r("ستظهر هنا الشبكات التي تراها الكاميرا", R, y + 5, 14, (110, 104, 94))
        y += 42
        # كلمة المرور
        self.put_r("الرمز", R, y + 11, 15, DIM)
        self.button(pygame.Rect(L, y, 80, 44), "إخفاء" if self.show_pw else "إظهار",
                    lambda: setattr(self, "show_pw", not self.show_pw), size=15)
        self.field("pass", pygame.Rect(L + 90, y, R - 70 - (L + 90), 44), "كلمة مرور الشبكة", password=True)
        y += 56
        self.button(pygame.Rect(L, y, R - L, 44), "جارٍ الإرسال…" if self._sending else "إرسال الشبكة إلى الكاميرا",
                    self.send, enabled=not self._sending, size=17)
        y += 54
        # تعليمات أول مرة (الأسماء اللاتينية في سطر مستقل)
        self.put_r("أول مرة: شغّل الكاميرا وصِل الحاسوب بشبكتها:", R, y, 14, DIM)
        self.put_r(f"{esp.AP_SSID}   |   {esp.AP_PASSWORD}", R, y + 20, 14, DIM)
        self.put_r("ثم اضغط «بحث» واكتب شبكتك وأرسلها، وبعدها تتصل الكاميرا وحدها دائماً", R, y + 40, 14, DIM)

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
        if name == "esp" and self.cam_info is None and not (self._searching or self._waiting):
            self.search()
        elif name != "esp" and not self._waiting:
            self.set_status("")
        elif name == "laptop" and len(self.cams) == 0:
            self.set_status("لم أجد كاميرا في هذا الحاسوب — جرّب كاميرا ESP أو الفأرة.", "warn")

    def search(self):
        if self._searching:
            return
        self._searching = True
        self._search_id += 1
        sid = self._search_id
        self.set_status("أبحث عن الكاميرا على الشبكة…", "dim")
        last = host_of(self.vals["address"]) or self.prefs.get("esp_host")

        def work():
            try:
                infos = esp.find_cameras(last_host=last, stop=self.stop,
                                         progress=lambda d, t: self.q.put(("progress", sid, d, t)))
            except Exception:
                infos = []
            self.q.put(("found", sid, infos))

        threading.Thread(target=work, daemon=True).start()

    def send(self):
        ssid, pw = self.vals["ssid"].strip(), self.vals["pass"]
        err = esp.validate_wifi(ssid, pw)
        if err:
            self.set_status(err, "err")
            return
        host = (self.cam_info or {}).get("host") or host_of(self.vals["address"])
        if not host:
            self.set_status("اضغط «بحث» أولاً، أو اكتب عنوان الكاميرا.", "err")
            return
        self._sending = True
        self.set_status("أرسل الشبكة إلى الكاميرا…", "dim")

        def work():
            ok, msg = esp.provision(host, ssid, pw)
            self.q.put(("sent", ok, msg, ssid))

        threading.Thread(target=work, daemon=True).start()

    def _wait_for_camera(self):
        self._waiting = True

        def work():
            info = esp.wait_for_camera(timeout=90, stop=self.stop, status=lambda t: self.q.put(("waiting", t)))
            self.q.put(("ready", info))

        threading.Thread(target=work, daemon=True).start()

    def start(self):
        src = self.source
        self.prefs.update(source=src, fullscreen=self.fullscreen)
        if src == "laptop":
            self.prefs["laptop_index"] = self.cam_index
            self.result = {"use_camera": True, "camera": self.cam_index}
        elif src == "esp":
            url = esp.normalize_source(self.vals["address"])
            if not url:
                self.set_status("اضغط «بحث» أو اكتب عنوان الكاميرا.", "err")
                return
            self.prefs["esp_host"] = host_of(self.vals["address"])
            self.result = {"use_camera": True, "camera": url}
        else:
            self.result = {"use_camera": False, "camera": 0}
        self.result["fullscreen"] = self.fullscreen
        if self.vals["ssid"].strip():
            self.prefs["esp_ssid"] = self.vals["ssid"].strip()
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
        elif t == pygame.TEXTINPUT:
            self._type(ev.text)
        elif t == pygame.KEYDOWN:
            self._key(ev)

    def _type(self, text):
        if self.focus is None or self.source != "esp":
            return
        text = "".join(c for c in text if c >= " " and c != "\x7f")
        limit = {"address": 120, "ssid": 64, "pass": 63}[self.focus]
        self.vals[self.focus] = (self.vals[self.focus] + text)[:limit]

    def _key(self, ev):
        k = ev.key
        ctrl = ev.mod & (pygame.KMOD_CTRL | pygame.KMOD_META)
        order = ["address", "ssid", "pass"]
        if k == pygame.K_ESCAPE:
            if self.focus:
                self.focus = None
            else:
                self.on_close()
        elif k == pygame.K_TAB and self.source == "esp":
            cur = order.index(self.focus) if self.focus in order else -1
            step = -1 if ev.mod & pygame.KMOD_SHIFT else 1
            self.focus = order[(cur + step) % 3]
        elif k == pygame.K_BACKSPACE and self.focus:
            self.vals[self.focus] = self.vals[self.focus][:-1]
        elif (k == pygame.K_v and ctrl) or (k == pygame.K_INSERT and ev.mod & pygame.KMOD_SHIFT):
            if self.focus:
                self._type(read_clipboard().replace("\r", "").split("\n")[0])
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if self.source == "esp" and self.focus == "address":
                self.focus = None
                self.search()
            elif self.source == "esp" and self.focus == "ssid":
                self.focus = "pass"
            elif self.source == "esp" and self.focus == "pass":
                self.focus = None
                self.send()
            else:
                self.start()

    # ------------------------------------------------------------------ خيوط → واجهة
    def _apply_camera(self, info, count=1):
        self.cam_info = info
        self.vals["address"] = info["host"]
        host = info["host"]
        if info.get("mode") == "sta":
            self.set_status("✓ الكاميرا متصلة بشبكتك" + (f" (وُجدت {count} كاميرات، اخترتُ الأولى)" if count > 1 else ""),
                            "ok", f"{info.get('ssid', '')}   |   {host}")
        else:
            self.set_status("الكاميرا في وضع الإعداد (أنت متصل بشبكتها). اختر شبكتك واكتب كلمتها ثم اضغط «إرسال»، "
                            "أو اضغط «ابدأ» للعمل مباشرة عبر شبكتها.", "warn")

        def nets():
            self.q.put(("nets", esp.networks(host, info.get("cfg_port", esp.CFG_PORT))))

        threading.Thread(target=nets, daemon=True).start()

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
                elif kind == "progress":
                    if msg[1] == self._search_id and self._searching:
                        self.set_status(f"أفحص الشبكة… {msg[2]} من {msg[3]}", "dim")
                elif kind == "found":
                    if msg[1] != self._search_id:
                        continue
                    self._searching = False
                    if msg[2]:
                        self._apply_camera(msg[2][0], len(msg[2]))
                    elif not self._waiting:
                        self.cam_info = None
                        self.set_status("لم أجد الكاميرا. تأكد أنها تعمل وأن الحاسوب على نفس شبكتها، "
                                        "أو على شبكة الإعداد في أول مرة (الاسم أسفل)، ثم اضغط «بحث».", "warn")
                elif kind == "nets":
                    self.nets = [n["ssid"] for n in msg[1]]
                    if self.nets and not self.vals["ssid"].strip():
                        self.vals["ssid"] = self.nets[0]
                elif kind == "sent":
                    _, ok, text, ssid = msg
                    self._sending = False
                    if ok:
                        self.cam_info = None
                        self.set_status("تم ✓ الكاميرا تعيد التشغيل وتتصل بالشبكة. صِل الحاسوب بنفس الشبكة الآن، "
                                        "وأنتظر ظهور الكاميرا…", "ok", ssid)
                        self._wait_for_camera()
                    else:
                        self.set_status(f"لم يتم الإرسال: {text}", "err")
                elif kind == "waiting":
                    if self._waiting:
                        self.set_status(f"أنتظر الكاميرا على شبكتك… {msg[1]} ث (الحاسوب يجب أن يكون على نفس الشبكة)", "dim")
                elif kind == "ready":
                    self._waiting = False
                    if msg[1]:
                        self._apply_camera(msg[1])
                    else:
                        self.set_status("لم تظهر الكاميرا على شبكتك. تأكد من اسم الشبكة وكلمتها وأن الحاسوب على نفس "
                                        "الشبكة. إن كانت خاطئة فستعود الكاميرا إلى شبكة الإعداد، فأعد المحاولة من هناك.",
                                        "err")
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
