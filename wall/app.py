"""
التطبيق الرئيسي: حلقة الرسم، آلة الحالات، الإيماءات، الواجهة العربية.

الحالات (سيناريو التفاعل):
  idle      لا أحد أمام الجدار        → كتلة نصية ساكنة على الشبكة (تتنفّس ببطء)
  presence  شخص في المنطقة البعيدة    → نبض واهتزاز خفيف لجذب الانتباه
  active    شخص في منطقة التفاعل      → تفكك الحروف وتجمعها حول اليد والظل
  reset     مغادرة المجال             → عودة تدريجية للشبكة

الإيماءات:
  موضع اليد       → مجال مغناطيسي + عدسة مكبّرة
  قبضة / كف مفتوحة → وزن الخط من Thin إلى Black
  إغلاق اليد قرب الحروف (قبضة أو قرص) → إمساكها وسحبها، وفتح اليد يرميها
  سرعة اليد        → موجة حركية وميلان (Kinetic)
  الكفّان معاً تتباعدان بسرعة → الشريحة التالية (الصفحة تنفتح كبابين)
  الكفّان معاً تتقاربان بسرعة → الشريحة السابقة (البابان ينغلقان)

الشرائح: خمس كلمات × (عربي ← مسماري ← صورة حقيقية) = 15 شريحة (wall/pages.py)
"""

import collections
import math
import os
import time

import numpy as np
import pygame

from PIL import Image, ImageOps

from . import artworks, config, settings, tiles
from .arabic import FontStack, GlyphCache, TextRenderer
from .audio import Audio
from .interaction import InteractionMixin
from .rendering import RenderingMixin
from .timing import FixedStepper
from .living import LivingMotion, ElasticWords
from .transitions import ParticleTransition
from .layout import build_layout
from .particles import ParticleField
from .recorder import Recorder
from .pages import CUNEIFORM_FONTS, PAGES
from .tracking import CameraTracker, MouseInput, TrackState

AUTO_ADVANCE_SECONDS = 25.0
ROOT = os.path.dirname(os.path.dirname(__file__))


def _lerp(a, b, t):
    return a + (b - a) * t


class App(InteractionMixin, RenderingMixin):
    def __init__(self, use_camera=True, fullscreen=False, camera_index=config.CAMERA_INDEX):
        pygame.init()
        pygame.display.set_caption("جدار التيبوغرافيا التوليدية التفاعلية")
        self.fullscreen = fullscreen
        self._open_window()
        self.clock = pygame.time.Clock()

        self.fonts = FontStack(config.FONT_CANDIDATES + [CUNEIFORM_FONTS[0]])   # خطوط الواجهة
        self.ui = TextRenderer(self.fonts)
        self.page_fonts = {p["id"]: FontStack(p["fonts"]) for p in PAGES if p["kind"] == "text"}

        saved = settings.load()
        self.n_words = len(PAGES[0]["items"])
        self.n_slides = self.n_words * len(PAGES)
        self.slide = int(saved.get("slide", 0)) % self.n_slides   # الصفحة = slide % 3، الكلمة = slide // 3
        self.transition = None
        self._pending = None
        self._span_hist = collections.deque()   # (زمن، المسافة بين الكفّين، x اليسرى، x اليمنى)
        self._gesture_ready_at = 0.0
        self._last_gesture = (-1e9, 0)
        self._gesture_intent = False
        self._settle_start = -1e9
        self.hand_gain = 1.0
        self._hands_since = None      # منذ متى تُرى يد (لتفعيل التفاعل باليد من بعيد)
        self._hands_last = -1e9          # تأثير اليدين على الحروف (يخفت بعد كل انتقال ثم يعود)
        # تُستخدم العتبة المحفوظة فقط إن عايرها المستخدم بنفسه (K أو [ ])
        self.calibrated = bool(saved.get("calibrated"))
        self.near_threshold = float(saved["near_threshold"]) if self.calibrated else config.NEAR_THRESHOLD
        self.word_mode = saved.get("word_mode", config.WORD_MODE)
        self._set_sensitivity(saved.get("sensitivity", config.HAND_SENSITIVITY))
        self.trails = config.TRAILS
        self.show_help = True
        self.show_camera = False
        self.show_debug = False
        self.show_grid = config.SHOW_GRID_GUIDES
        self.message = None
        self.message_until = 0.0

        # الصوت: المقدمة أولاً ثم شرح الشريحة الحالية
        self.audio = Audio(sound=saved.get("sound", config.SOUND_ENABLED),
                           guide=saved.get("guide", config.GUIDE_ENABLED))
        self._guide_queue = ["intro", self._guide_key()]
        self._guide_due = 1.5
        self._guide_done_at = -1e9

        self.mouse = MouseInput(self.size)
        self.tracker = None
        self.camera_index = camera_index
        if use_camera:
            self.tracker = CameraTracker(camera_index)
            self.tracker.start()

        # حالة التفاعل
        self.state = "idle"
        self.activity = 0.0
        self.presence = 0.0
        self.prox_smooth = 0.0
        self.last_seen = -1e9
        self.near = False
        self.global_weight = float(config.REST_WEIGHT)
        self.hand_filters = {}     # رقم اليد → HandFilter
        self.hand_screen = []      # [(x, y, openness, closed)] على الشاشة بعد التنعيم
        self.hand_speed = 0.0
        self.ripple = 0.0
        self.t = 0.0
        self.last_switch = 0.0
        self.help_until = 12.0     # تختفي المساعدة تلقائياً بعد 12 ثانية
        self._attractors = []
        self._holds = {}
        self._hover = set()
        self._silhouette = None
        self._track = None

        # التسجيل
        self.writer = None
        self.record_path = None
        self._last_record = 0.0

        self._load_item(entrance=True)
        # Keep Python GC defaults; freezing process-wide objects hides cycles.

    # ------------------------------------------------------------------
    # الشرائح
    # ------------------------------------------------------------------
    @property
    def page_idx(self):
        return self.slide % len(PAGES)

    @property
    def word_idx(self):
        return (self.slide // len(PAGES)) % self.n_words

    @property
    def page(self):
        return PAGES[self.page_idx]

    @property
    def pal(self):
        return self.page["palette"]

    def go_slide(self, target, style=None):
        """
        طلب الانتقال إلى شريحة: style="open" (البابان ينفتحان — للأمام)
        أو "close" (البابان ينغلقان — للخلف). يُنفَّذ في الإطار التالي.
        """
        target %= self.n_slides
        if target == self.slide or self.transition or self._pending:
            return
        if style is None:
            style = "open" if target > self.slide else "close"
        self._pending = (target, style)

    def _guide_key(self):
        return f"{self.word_idx}_{self.page['id']}"

    def _guide_step(self):
        """يشغّل نص الدليل التالي في الطابور حين يسكت الدليل ويستقر العرض."""
        if self.audio.speaking:
            self._guide_done_at = self.t
            return
        if not self._guide_queue or self.transition or self._pending or self.t < self._guide_due:
            return
        self.audio.narrate(self._guide_queue.pop(0))
        self._guide_due = self.t + 0.6            # فاصل قصير بين نصّين متتاليين

    def _begin_transition(self):
        target, style = self._pending
        self._pending = None
        self.audio.play("slide_next" if style == "open" else "slide_prev", min_gap=0.3)
        self.audio.stop_voice()
        # Capture the artwork only; the HUD remains readable above the transition.
        self._draw_content(transition=True)
        old, old_bg = self.screen.copy(), self.pal["bg"]
        self.field.release_all()
        self.slide = target
        self._load_item(entrance=False)
        self._draw_content(transition=True)
        new = self.screen.copy()
        effect = ParticleTransition(old,new,old_bg,self.pal["bg"],config.PAGE_TRANSITION)
        effect.background = self._clay_bg
        self.transition = {"effect":effect,"t0":self.t}
        self.screen.blit(old,(0,0))
        self._hint_t0 = self.t
        self._guide_queue = [self._guide_key()]
        self._guide_due = self.t + config.PAGE_TRANSITION + config.GUIDE_DELAY
        self._save_settings()

    # ------------------------------------------------------------------
    # إعداد النافذة والنص
    # ------------------------------------------------------------------
    def _open_window(self):
        if self.fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode(config.WINDOW_SIZE, pygame.RESIZABLE)
        self.size = self.screen.get_size()

    def _load_item(self, entrance=True, direction=0):
        page = self.page
        item = page["items"][self.word_idx]
        w, h = self.size
        pal = self.pal
        if page["kind"] == "image":
            img = artworks.get(item["art"]) if "art" in item else ImageOps.exif_transpose(Image.open(item["path"]))
            self.layout, surf, grid = tiles.build_image_layout(img, w, h)
            self.glyphs = tiles.TileCache(surf, self.layout.font_size, grid, pal["accent"])
            self.glyphs.warm(set(g.ch for g in self.layout.glyphs))
        else:
            fonts = self.page_fonts[page["id"]]
            # الجملة العربية بسطرين، والكلمة المسمارية وحدها بحجم كبير يملأ اللوح
            self.layout = build_layout(item["text"], fonts, w, h, rtl=page["rtl"],
                                       max_size=0.2 if page["rtl"] else 0.34,
                                       keyword=item.get("keyword"),
                                       word_mode=self.word_mode and page["rtl"])
            self.glyphs = GlyphCache(fonts, self.layout.font_size, [pal["ink"], pal["accent"]])
            self.glyphs.warm(set(g.ch for g in self.layout.glyphs))
        self.living = LivingMotion()
        self.elastic = ElasticWords()
        self._elastic_cache = {}
        self.physics = FixedStepper(config.PHYSICS_HZ, config.PHYSICS_MAX_STEPS)
        self._physics_time = getattr(self, "t", 0.0)
        self._holds = {}
        for hf in getattr(self, "hand_filters", {}).values():
            hf.closed = False
        # مسافة التنافر حسب العرض الفعلي للحروف/العلامات (العلامات المسمارية عريضة)
        advs = sorted(g.adv for g in self.layout.glyphs) or [self.layout.font_size]
        spacing = max(self.layout.font_size * 0.8, advs[len(advs) // 2] * 1.05)
        self.field = ParticleField(self.layout.glyphs, self.size,
                                   entrance=entrance and not direction, spacing=spacing)
        if direction:
            # دخول العنصر الجديد من جهة الحركة
            self.field.pos[:, 0] -= direction * w * 0.75
            self.field.vel[:, 0] += direction * w * 1.2
        self.author = item.get("author", "")
        self.credit = item.get("credit", "")
        self.translit = item.get("translit", "")
        self.last_switch = self.t if hasattr(self, "t") else 0.0

    def _resize(self, size):
        self.size = size
        self.transition = self._pending = None
        self._reset_hands()
        self.mouse.resize(size)
        self._stop_recording()
        self._load_item(entrance=False)

    def _flash(self, text, seconds=2.0):
        self.message = text
        self.message_until = time.time() + seconds

    def _set_sensitivity(self, value):
        """حساسية حركة اليد: كم تُكبَّر حركتها على الشاشة (منطقة التفاعل في وسط صورة الكاميرا)."""
        lo, hi = config.HAND_SENSITIVITY_RANGE
        self.sensitivity = round(min(max(float(value), lo), hi), 2)
        config.INTERACTION_BOX = config.interaction_box(self.sensitivity)   # يقرؤها خيط التتبع في كل إطار

    def _save_settings(self):
        data = {"slide": self.slide, "word_mode": self.word_mode, "sensitivity": self.sensitivity,
                "sound": self.audio.sound, "guide": self.audio.guide}
        if self.calibrated:
            data.update(calibrated=True, near_threshold=round(self.near_threshold, 3))
        settings.save(data)

    # ------------------------------------------------------------------
    # المدخلات
    # ------------------------------------------------------------------
    def _handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type == pygame.VIDEORESIZE and not self.fullscreen:
                # SDL/Pygame already resizes the display surface. Recreating it here
                # can feed another resize event back into the window manager.
                self.screen = pygame.display.get_surface()
                if self.screen.get_size() != self.size:
                    self._resize(self.screen.get_size())
            elif ev.type == pygame.MOUSEMOTION:
                self.mouse.mouse = (ev.pos[0] / self.size[0], ev.pos[1] / self.size[1])
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    self.mouse.left = True
                elif ev.button == 3:
                    self.mouse.right = True
            elif ev.type == pygame.MOUSEBUTTONUP:
                if ev.button == 1:
                    self.mouse.left = False
                elif ev.button == 3:
                    self.mouse.right = False
            elif ev.type == pygame.MOUSEWHEEL:
                self.mouse.nudge(ev.y * 0.02)
            elif ev.type == pygame.KEYDOWN and not getattr(ev, "repeat", False):
                if not self._on_key(ev.key):
                    return False
        return True

    def _on_key(self, key):
        if key in (pygame.K_ESCAPE, pygame.K_q):
            return False
        if key == pygame.K_f:
            self.fullscreen = not self.fullscreen
            self._open_window()
            self._resize(self.size)
        elif key == pygame.K_RIGHT:
            self.go_slide(self.slide + 1, "open")
        elif key == pygame.K_LEFT:
            self.go_slide(self.slide - 1, "close")
        elif key in (pygame.K_1, pygame.K_2, pygame.K_3):
            k = {pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2}[key]
            self.go_slide(self.word_idx * len(PAGES) + k)
        elif key == pygame.K_n:
            self.go_slide(self.slide + len(PAGES), "open")
        elif key == pygame.K_SPACE and not self.tracker:
            if self.mouse.anchor is None:
                # Anchor the opposite end of the currently held word for testing.
                ids = np.flatnonzero(self.field.held>=0)
                if ids.size:
                    i=int(ids[0]);x,y=self.field.pos[i]
                    side=-1 if self.mouse.mouse[0]*self.size[0]>x else 1
                    self.mouse.anchor=((x+side*self.field.widths[i]*.5)/self.size[0],y/self.size[1])
                else:
                    self.mouse.anchor=self.mouse.mouse
            else:
                self.mouse.anchor=None
        elif key == pygame.K_0:
            self._reset_hands()
            self._load_item(entrance=False)
            self._flash("إعادة ترتيب اللوحة")
        elif key == pygame.K_b:
            if not self.transition and not self._pending:
                self.word_mode = not self.word_mode
                self._load_item(entrance=False)
                self._save_settings()
                self._flash("وضع الكلمات" if self.word_mode else "وضع الحروف")
        elif key == pygame.K_a:
            self.audio.guide = not self.audio.guide
            self.audio.play("guide_on" if self.audio.guide else "guide_off", min_gap=0.0)
            if self.audio.guide:
                self._guide_queue = [self._guide_key()]
                self._guide_due = self.t + 0.9
            else:
                self.audio.stop_voice()
            self._save_settings()
            self._flash("الدليل الصوتي: " + ("يعمل" if self.audio.guide else "متوقف"))
        elif key == pygame.K_p:
            if self.audio.guide:          # إعادة شرح الشريحة الحالية
                self.audio.stop_voice(200)
                self._guide_queue = [self._guide_key()]
                self._guide_due = self.t + 0.3
        elif key == pygame.K_x:
            self.audio.set_sound(not self.audio.sound)
            if self.audio.sound and self.audio.guide:
                self._guide_queue = [self._guide_key()]
                self._guide_due = self.t + 0.5
            self._save_settings()
            self._flash("الصوت: " + ("يعمل" if self.audio.sound else "مكتوم"))
        elif key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_MINUS, pygame.K_KP_MINUS):
            up = key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS)
            self._set_sensitivity(self.sensitivity + (0.1 if up else -0.1))
            self._save_settings()
            self._flash(f"حساسية حركة اليد: {self.sensitivity:.1f}× (حركة أصغر تغطي الشاشة كلما زادت)", 2.5)
        elif key == pygame.K_g:
            self.show_grid = not self.show_grid
        elif key == pygame.K_v:
            self.trails = False
            self._flash("أثر الحركة: " + ("مفعّل" if self.trails else "متوقف"))
        elif key == pygame.K_h:
            visible = self.show_help and self.t < self.help_until
            self.show_help = not visible
            self.help_until = self.t + 1e9 if self.show_help else 0.0
        elif key == pygame.K_c:
            self.show_camera = not self.show_camera
        elif key == pygame.K_d:
            self.show_debug = not self.show_debug
        elif key == pygame.K_k:
            self._calibrate()
        elif key == pygame.K_m:
            self._reset_hands()
            if self.tracker:
                self.tracker.stop()
                self.tracker = None
                self._flash("وضع الفأرة: العجلة للاقتراب، الزر الأيسر للإمساك، الأيمن للظل", 4)
            else:
                self.tracker = CameraTracker(self.camera_index)
                self.tracker.start()
                self._flash("وضع الكاميرا")
        elif key == pygame.K_LEFTBRACKET:
            self.near_threshold = max(0.05, self.near_threshold - 0.01)
            self.calibrated = True
            self._save_settings()
            self._flash(f"حد التفاعل: {self.near_threshold:.2f}")
        elif key == pygame.K_RIGHTBRACKET:
            self.near_threshold = min(0.6, self.near_threshold + 0.01)
            self.calibrated = True
            self._save_settings()
            self._flash(f"حد التفاعل: {self.near_threshold:.2f}")
        elif key == pygame.K_UP:
            self.mouse.nudge(0.03)
        elif key == pygame.K_DOWN:
            self.mouse.nudge(-0.03)
        elif key == pygame.K_s:
            self.save_screenshot()
        elif key == pygame.K_r:
            if self.writer:
                self._stop_recording()
            else:
                self._start_recording()
        return True

    def _calibrate(self):
        """الوقوف حيث يجب أن يبدأ التفاعل ثم الضغط على K."""
        if self.prox_smooth < 0.03:
            self._flash("قف أمام الكاميرا في موضع بدء التفاعل ثم اضغط K", 3)
            return
        self.near_threshold = float(np.clip(self.prox_smooth * 0.92, 0.05, 0.6))
        self.calibrated = True
        self._save_settings()
        self._flash(f"تمت المعايرة — حد التفاعل: {self.near_threshold:.2f}", 3)

    # ------------------------------------------------------------------
    # اللقطات والتسجيل
    # ------------------------------------------------------------------
    def save_screenshot(self, path=None):
        if path is None:
            folder = os.path.join(settings.DATA_DIR, "screenshots")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, time.strftime("wall_%Y%m%d_%H%M%S.png"))
        pygame.image.save(self.screen, path)
        self._flash("تم حفظ اللقطة")
        return path

    def _start_recording(self):
        folder = os.path.join(settings.DATA_DIR, "recordings")
        os.makedirs(folder, exist_ok=True)
        self.record_path = os.path.join(folder, time.strftime("wall_%Y%m%d_%H%M%S.mp4"))
        self.writer = Recorder(self.record_path, self.size, config.RECORD_FPS)
        if not self.writer.ok:
            self.writer = None
            self._flash("تعذّر بدء التسجيل")
            return
        self._last_record = self.t
        self._flash("بدأ التسجيل — اضغط R للإيقاف")

    def _stop_recording(self):
        if self.writer:
            ok = self.writer.close()
            self.writer = None
            self._flash("تم حفظ الفيديو في مجلد recordings" if ok else "تعذّر إكمال التسجيل — راجع السجل", 3)

    def _capture_frame(self):
        if not self.writer or self.t - self._last_record < 1.0 / config.RECORD_FPS:
            return
        self._last_record += 1.0 / config.RECORD_FPS
        if self.t - self._last_record > 0.5:      # لا نلاحق تأخراً كبيراً
            self._last_record = self.t
        self.writer.push(pygame.image.tobytes(self.screen, "RGB"), self.size)

    def current_input(self) -> TrackState:
        if self.tracker:
            self.tracker.want_preview = self.show_camera
            s = self.tracker.snapshot()
            if s.error:
                # تراجع تلقائي لوضع الفأرة عند تعذّر الكاميرا
                self.tracker.stop()
                self.tracker = None
                self._flash(s.error + " — تم التحويل لوضع الفأرة", 6)
                self._reset_hands()
                return self.mouse.snapshot()
            return s
        return self.mouse.snapshot()

    # ------------------------------------------------------------------
    # التحديث
    # ------------------------------------------------------------------
    def update(self, dt, track: TrackState):
        self._frame_dt = dt
        self.t += dt
        tr = self.transition
        if tr and self.t - tr["t0"] >= config.PAGE_TRANSITION:
            # انتهى الانتقال → مهلة سكون قبل أن تؤثر اليدان من جديد
            self.transition = None
            self._settle_start = self.t
            self._gesture_ready_at = max(self._gesture_ready_at, self.t + config.SLIDE_GESTURE_DELAY)
        self._update_hand_gain(dt)
        now = self.t          # زمن المحاكاة (مستقل عن سرعة الجهاز)
        W, H = self.size

        if track.person:
            self.last_seen = now
        present = (now - self.last_seen) < config.LOST_TIMEOUT
        target_prox = track.proximity if track.person else (self.prox_smooth if present else 0.0)
        self.prox_smooth = _lerp(self.prox_smooth, target_prox, min(1.0, dt * 6))

        # اليد المرفوعة تفعّل التفاعل من أي مسافة (لا يلزم الاقتراب حتى حد منطقة التفاعل)
        if track.hands:
            if self._hands_since is None:
                self._hands_since = now
            self._hands_last = now
        elif now - self._hands_last > 0.3:
            self._hands_since = None
        hands_engaged = (self._hands_since is not None and now - self._hands_since > config.HANDS_ACTIVATE
                         and now - self._hands_last < 1.5)

        # منطقة التفاعل مع تخلّف (Hysteresis) لتجنّب التذبذب
        if self.near:
            close = self.prox_smooth > self.near_threshold - config.HYSTERESIS
        else:
            close = self.prox_smooth > self.near_threshold + config.HYSTERESIS * 0.3
        self.near = present and (close or hands_engaged)

        prev_state = self.state
        if self.near:
            self.state = "active"
        elif present:
            self.state = "presence"
        elif self.activity > 0.05:
            self.state = "reset"
        else:
            self.state = "idle"
        if self.state == "active" and prev_state != "active":
            self.field.scatter(0.15)          # لحظة التفكك والتناثر
            self._hint_t0 = self.t
            self.audio.play("awaken", 0.8, min_gap=4.0)

        rate = config.ACTIVITY_RISE if self.near else config.ACTIVITY_FALL
        self.activity = _lerp(self.activity, 1.0 if self.near else 0.0, min(1.0, dt * rate))
        self.presence = _lerp(self.presence, 1.0 if present else 0.0, min(1.0, dt * 2.0))

        self._update_hands(dt, track, W, H)

        # وزن الخط العام: القبضة = Thin، الكف المفتوحة = Black
        rest_w = config.REST_WEIGHT
        if config.BREATHING:
            # تنفّس بطيء للخط المتغيّر في السكون
            rest_w += config.BREATH_AMPLITUDE * math.sin(2 * math.pi * self.t / config.BREATH_PERIOD)
        if self.hand_screen and self.activity > 0.2:
            op = sum(h[2] for h in self.hand_screen) / len(self.hand_screen)
            hand_w = _lerp(config.WEIGHT_LEVELS[0], config.WEIGHT_LEVELS[-1], op)
            target_w = _lerp(rest_w, hand_w, self.hand_gain)
        else:
            target_w = rest_w
        self.global_weight = _lerp(self.global_weight, target_w, min(1.0, dt * 4))

        attractors = []
        if self.activity > 0.01:
            g = self.hand_gain
            attractors = [(hf.dx, hf.dy, (config.GRAB_MAGNET if hf.closed else 1.0) * g)
                          for hf in self.hand_filters.values()
                          if self.t - hf.seen <= config.HAND_FRESH_TIMEOUT]
        sil = track.silhouette if self.state in ("active", "reset", "presence") else None
        fresh = [h for h in self.hand_filters.values() if self.t-h.seen <= config.HAND_FRESH_TIMEOUT]
        enabled = self.transition is None and self.hand_gain > .9
        self.living.update(self.t,fresh,self.field,enabled)
        self.living.update_contour(sil if enabled and self.activity>.4 else None,self.t,self.size)
        center = self.elastic.update(self.field,fresh,dt,
                                    enabled and self.word_mode and self.page["id"]=="arabic")
        if center is not None:
            self._holds[self.elastic.owner] = center
        if self.transition is None:          # الصفحة ثابتة أثناء الانتقال
            def physics_step(step):
                self._physics_time += step
                motion = max((math.hypot(h.vx,h.vy)/max(W*.5,1) for h in fresh),default=0.0)
                motion = max(motion, .45 if self._holds else 0.0,
                             .7 if self.living.contour is not None else 0.0)
                dynamic = self.activity*float(np.clip(motion,0,1))*self.hand_gain
                force = self.living.forces(self.field,fresh,self.t,
                                           self.activity*self.hand_gain,self.page["kind"]=="image")
                self.field.step(step,self._physics_time,dynamic,self.presence,
                                [],None,holds=self._holds,extra_force=force)
            self.physics.advance(dt, physics_step)
        self._attractors = attractors
        self._silhouette = track.silhouette
        self._track = track

        self._guide_step()
        self.audio.update(dt)

        # تقليب عناصر الصفحة تلقائياً عند السكون (مع الدليل: بعد انتهاء الشرح بمهلة)
        if self.audio.guide and self.audio.sound and self.audio.ok:
            narrating = self.audio.speaking or bool(self._guide_queue)
            wait_over = (not narrating and now - max(self._guide_done_at, self.last_switch)
                         > config.GUIDE_AUTO_ADVANCE)
        else:
            wait_over = now - self.last_switch > AUTO_ADVANCE_SECONDS
        if self.state == "idle" and wait_over and not self.transition:
            self.go_slide(self.slide + 1, "open")

    # ------------------------------------------------------------------
    # الرسم
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def run(self):
        running = True
        try:
            while running:
                dt = min(self.clock.tick(config.FPS) / 1000.0, 0.25)
                running = self._handle_events()
                if not running:
                    break
                t0 = time.perf_counter()
                self.update(dt, self.current_input())
                self.draw()
                # تجهيز تدريجي للحروف في الوقت المتبقي من الإطار (بلا تجمّد)
                spare = 1.0 / config.FPS - (time.perf_counter() - t0) - 0.004
                self.glyphs.pump(max(0.0015, spare))
                self._capture_frame()          # الفيديو بدون شارة التسجيل
                self._draw_recording_badge()
                pygame.display.flip()
        finally:
            self._stop_recording()
            self._save_settings()
            if self.tracker:
                self.tracker.stop()
            pygame.quit()
