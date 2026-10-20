"""Display composition and visual effects; no camera ownership."""
import math
import time
import numpy as np
import pygame
from . import config
from .pages import PAGES

def _lerp(a, b, t):
    return a + (b-a)*t

STATE_NAMES = {
    "idle": "سكون — ارفع يدك",
    "presence": "تم رصد اليد",
    "active": "تفاعل — منطقة الحركة",
    "reset": "انسحاب — عودة إلى الشبكة",
}

PRESENCE_HINT = "اقترب أكثر، وارفع يدك"
GESTURE_HINT = "باعِد بين كفّيك المفتوحتين بسرعة للتالي، وقرّبهما للسابق"

HELP_LINES = [
    ("F", "ملء الشاشة"),
    ("← →", "السابق / التالي (أو قرّب وباعِد كفّيك)"),
    ("1 2 3", "عربي / مسماري / صورة للكلمة نفسها"),
    ("N", "الكلمة التالية في الصفحة نفسها"),
    ("G", "إظهار الشبكة التصميمية"),
    ("B", "التبديل بين الحروف والكلمات"),
    ("0", "إعادة ترتيب اللوحة"),
    ("Space", "يد ثانية ثابتة — بالفأرة فقط"),
    ("C", "معاينة الكاميرا"),
    ("D", "معلومات المعايرة"),
    ("K", "معايرة تلقائية من موضعك الحالي"),
    ("[ ]", "ضبط حد منطقة التفاعل يدوياً"),
    ("M", "التبديل بين الكاميرا والفأرة"),
    ("A", "الدليل الصوتي: تشغيل / إيقاف"),
    ("P", "إعادة شرح الشريحة"),
    ("X", "كتم الصوت / تشغيله"),
    ("S", "حفظ لقطة شاشة"),
    ("R", "بدء / إيقاف تسجيل فيديو"),
    ("H", "إخفاء / إظهار المساعدة"),
    ("Esc", "خروج"),
]


class RenderingMixin:
    def draw(self):
        if self._pending:
            self._begin_transition()
        elif self.transition:
            self.transition["effect"].render(self.screen,self.t-self.transition["t0"])
        else:
            self._draw_content()
        self._draw_hands()
        self._draw_pages()
        self._draw_hud()

    def _draw_content(self, transition=False):
        from .clay import make_tablet
        cached = getattr(self, "_clay_bg", None)
        if cached is None or cached.get_size() != self.size:
            self._clay_bg = make_tablet(self.size)
        self.screen.blit(self._clay_bg, (0, 0))
        if self.show_grid:
            self._draw_grid()
        self._draw_glyphs()
        self._draw_author()

    def _draw_tablet(self):
        """لوح طيني خلف العلامات المسمارية."""
        x, y, w, h = self.layout.block
        m = self.layout.font_size * 0.45
        r = pygame.Rect(int(x - m), int(y - m * 0.6), int(w + 2 * m), int(h + m * 1.2))
        pal = self.pal
        pygame.draw.rect(self.screen, pal["panel"], r, border_radius=int(m * 0.8))
        pygame.draw.rect(self.screen, pal["panel_edge"], r, 3, border_radius=int(m * 0.8))
        inner = r.inflate(-int(m * 0.5), -int(m * 0.5))
        pygame.draw.rect(self.screen, pal["panel_edge"], inner, 1, border_radius=int(m * 0.6))

    def _draw_pages(self):
        """مؤشر الصفحات أعلى الوسط: نقاط + اسم الصفحة الحالية."""
        W = self.size[0]
        pal = self.pal
        n = len(PAGES)
        gap = 22
        x0 = W // 2 - gap * (n - 1) // 2
        for i in range(n):
            cx = x0 + i * gap
            if i == self.page_idx:
                pygame.draw.circle(self.screen, pal["ink"], (cx, 24), 6)
            else:
                pygame.draw.circle(self.screen, pal["dim"], (cx, 24), 5, 1)
        name = self.ui.render(self.page["name"], 15, 500, pal["accent"])
        self.screen.blit(name, name.get_rect(midtop=(W // 2, 36)))
        # الكلمات الخمس
        gap = 12
        x0 = W // 2 - gap * (self.n_words - 1) // 2
        for i in range(self.n_words):
            col = pal["ink"] if i == self.word_idx else pal["dim"]
            pygame.draw.circle(self.screen, col, (x0 + i * gap, 66), 3 if i == self.word_idx else 2)

    def _draw_shadow(self):
        sil = self._silhouette
        if sil is None or self.presence < 0.02:
            return
        # الظل سطح معتم يُدمج بـ"الأفتح" (MAX): أسرع بكثير من الشفافية لكل بكسل.
        # يُعاد بناؤه فقط عند وصول قناع جديد أو تغيّر شدة الحضور.
        level = round(self.presence * (0.45 if self.trails and self.activity > 0.02 else 1.0), 1)
        cached = getattr(self, "_shadow_cache", None)
        key = (self.size, level, self.page["id"])
        if cached is None or cached[0] is not sil or cached[1] != key:
            mask = np.clip(sil[0] * 0.9 * level, 0, 1)[..., None]
            bg = np.array(self.pal["bg"], np.float32)
            sh = np.array(self.pal["shadow"], np.float32)
            rgb = (bg + (sh - bg) * mask).astype(np.uint8)
            small = pygame.surfarray.make_surface(np.ascontiguousarray(rgb.transpose(1, 0, 2)))
            big = pygame.transform.smoothscale(small, self.size).convert()
            self._shadow_cache = (sil, key, big)
        self.screen.blit(self._shadow_cache[2], (0, 0), special_flags=pygame.BLEND_RGB_MAX)

    def _draw_grid(self):
        surf = pygame.Surface(self.size, pygame.SRCALPHA)
        col = (120, 110, 100, 70)
        x, y, w, h = self.layout.block
        for b in self.layout.baselines:
            pygame.draw.line(surf, col, (x, b), (x + w, b), 1)
        for cx in self.layout.columns:
            pygame.draw.line(surf, col, (cx, y - 20), (cx, y + h + 20), 1)
        pygame.draw.rect(surf, self.pal["accent"] + (90,), (x, y, w, h), 1)
        self.screen.blit(surf, (0, 0))

    def _draw_glyphs(self):
        f = self.field
        if f.n == 0:
            return
        W, H = self.size
        dt = getattr(self, "_frame_dt", 1.0 / config.FPS)
        attractors = self._attractors
        lens_r = config.LENS_RADIUS * W
        if attractors:
            lf, ldx = f.lens(attractors, lens_r)
            lf *= self.activity * self.hand_gain
        else:
            lf = np.zeros(f.n, np.float32)
            ldx = np.zeros(f.n, np.float32)
        held = f.held >= 0
        lf = np.where(held, 0.0, lf)
        # تنعيم العدسة لكل حرف → لا قفزات عند ارتجاف اليد
        ks = min(1.0, dt * 7)
        f.lens_f += (lf - f.lens_f) * ks
        f.lens_dx += (ldx - f.lens_dx) * ks
        lf = f.lens_f

        # الوزن والحجم لكل حرف (عدسة مكبّرة تحت اليد، والحروف الممسوكة ثقيلة)
        target_w = self.global_weight + lf * config.LENS_WEIGHT_BOOST + f.emph * config.KEYWORD_WEIGHT
        target_w = np.where(held, np.maximum(target_w, 800.0), target_w)
        f.weight += (target_w - f.weight) * min(1.0, dt * 6)
        target_s = 1.0 + lf * config.LENS_SCALE_BOOST
        hover = np.zeros(f.n, bool)
        for i in getattr(self, "_hover", ()):
            if i < f.n:
                hover[i] = True
        target_s = np.where(hover, np.maximum(target_s, 1.25), target_s)
        target_s = np.where(held, 1.35, target_s)
        f.scale += (target_s - f.scale) * min(1.0, dt * 6)

        wl = np.array(config.WEIGHT_LEVELS, np.float32)
        sl = np.array(config.SCALE_LEVELS, np.float32)
        widx = np.abs(f.weight[:, None] - wl[None, :]).argmin(1)
        sidx = np.abs(f.scale[:, None] - sl[None, :]).argmin(1)

        # تباعد حرفي (Tracking) حول اليد + موجة حركية (Kinetic)
        rx = f.pos[:, 0] + f.lens_dx * config.LENS_TRACKING * lf
        ry = f.pos[:, 1].copy()
        if self.ripple > 0.01:
            ry += self.ripple * self.layout.font_size * 0.5 * np.sin(f.pos[:, 0] * 0.012 - self.t * 7.0)
        target_a = np.clip(-f.vel[:, 0] * config.KINETIC_ROTATION, -config.MAX_ROTATION, config.MAX_ROTATION)
        target_a *= self.activity
        f.angle += (target_a - f.angle) * min(1.0, dt * 5)
        ang = f.angle
        accent = held | hover          # البرتقالي = الحرف الذي ستمسكه أو تمسكه

        blit = self.screen.blit
        get = self.glyphs.get
        for i, ch in enumerate(f.chars):
            stretch = self.elastic.stretch[i] if self.elastic.stretch is not None else 1.0
            stretching = stretch > 1.005 or self.elastic.index == i
            wi,si,angle = (10,0,0.0) if stretching else (int(widx[i]),int(sidx[i]),float(ang[i]))
            surf, ox, oy = get(ch,wi,si,1 if accent[i] else 0,angle)
            if stretch > 1.005:
                from .elastic_ink import stretch_ink
                key = (ch,wi,si,bool(accent[i]),round(float(stretch),2))
                result = self._elastic_cache.get(key)
                if result is None:
                    result = stretch_ink(surf,float(stretch))
                    if len(self._elastic_cache)>90:self._elastic_cache.clear()
                    self._elastic_cache[key] = result
                surf = result
                ox *= stretch
            blit(surf, surf.get_rect(center=(int(rx[i] + ox), int(ry[i] + oy))))

    def _draw_author(self):
        """التعليق تحت الكلمة/اللوح/الصورة: سطر عربي، ثم القراءة اللاتينية أو مصدر الصورة."""
        if not self.author:
            return
        alpha = int(255 * max(0.0, 1.0 - self.activity * 1.6))
        if alpha <= 5:
            return
        x, y, w, h = self.layout.block
        bottom = y + h + 18
        if self.page.get("tablet"):
            bottom += self.layout.font_size * 0.27          # أسفل اللوح الطيني
        size = 20 if self.page["kind"] == "image" else 26
        color = self.pal["accent"] if self.page["id"] == "arabic" else self.pal["ink"]
        surf = self.ui.render(self.author, size, 400, color)
        surf.set_alpha(alpha)
        rect = surf.get_rect(midtop=(x + w / 2, bottom))
        self.screen.blit(surf, rect)
        # سطر ثانٍ لاتيني بالكامل (لا يختلط اتجاهه مع العربية)
        second = getattr(self, "translit", "") or getattr(self, "credit", "")
        if second:
            small = 20 if self.translit else 13
            c = self.ui.render(second, small, 400, self.pal["dim"])
            c.set_alpha(alpha)
            self.screen.blit(c, c.get_rect(midtop=(rect.centerx, rect.bottom + 4)))

    def _draw_hands(self):
        a = self.activity * (0.3 + 0.7 * self.hand_gain)   # خافتة أثناء مهلة ما بعد الانتقال
        if a < 0.05:
            return
        for (x, y, o, closed) in self.hand_screen:
            if closed:
                # قبضة: دائرة صغيرة ممتلئة جزئياً
                r = 16
                surf = pygame.Surface((r * 2 + 6, r * 2 + 6), pygame.SRCALPHA)
                pygame.draw.circle(surf, self.pal["accent"] + (int(70 * a),), (r + 3, r + 3), r)
                pygame.draw.circle(surf, self.pal["accent"] + (int(230 * a),), (r + 3, r + 3), r, 2)
            else:
                r = int(20 + 26 * o)
                surf = pygame.Surface((r * 2 + 6, r * 2 + 6), pygame.SRCALPHA)
                pygame.draw.circle(surf, self.pal["accent"] + (int(150 * a),), (r + 3, r + 3), r, 2)
                pygame.draw.circle(surf, self.pal["accent"] + (int(220 * a),), (r + 3, r + 3), 4)
            self.screen.blit(surf, (int(x) - r - 3, int(y) - r - 3))

    def _draw_hud(self):
        W, H = self.size
        pad = 20
        # خلفية خفيفة خلف العنوان حتى لا يتلطخ مع أثر الحركة
        if self.trails and self.activity > 0.02:
            pygame.draw.rect(self.screen, self.pal["bg"], (W - 520, 0, 520, pad + 60))
        mode = "كاميرا" if self.tracker else "فأرة"
        top = self.ui.render("جدار التيبوغرافيا التوليدية التفاعلية", 22, 600, self.pal["ink"])
        self.screen.blit(top, top.get_rect(topright=(W - pad, pad)))
        audio = getattr(self, "audio", None)
        if audio and audio.ok:
            mode += "  ·  " + ("صامت" if not audio.sound else
                               "الدليل يتحدث…" if audio.speaking else
                               "الدليل الصوتي" if audio.guide else "صوت")
        st = self.ui.render(f"{STATE_NAMES[self.state]}  ·  {mode}", 16, 400, self.pal["accent"])
        self.screen.blit(st, st.get_rect(topright=(W - pad, pad + 34)))

        if self.show_help and (self.t < self.help_until) and self.activity < 0.1:
            y = pad + 70
            panel = pygame.Surface((300, len(HELP_LINES) * 22 + 16), pygame.SRCALPHA)
            panel.fill(self.pal["bg"] + (225,))
            self.screen.blit(panel, (W - pad - 300 + 8, y - 8))
            for key, label in HELP_LINES:
                s = self.ui.render(label, 14, 300, (170, 162, 150))
                k = self.ui.render(key, 14, 700, self.pal["ink"])
                self.screen.blit(s, s.get_rect(topright=(W - pad - 44, y)))
                self.screen.blit(k, k.get_rect(topright=(W - pad, y)))
                y += 22

        if self.show_debug:
            self._draw_debug()
        if self.show_camera:
            self._draw_camera()

        has_message = self.message and time.time() < self.message_until
        if has_message:
            m = self.ui.render(self.message, 18, 500, self.pal["ink"])
            bg = pygame.Surface((m.get_width() + 30, m.get_height() + 14), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 170))
            r = bg.get_rect(midbottom=(W // 2, H - 30))
            self.screen.blit(bg, r)
            self.screen.blit(m, m.get_rect(center=r.center))
        else:
            hint = None
            if self.tracker and (self._track is None or not self._track.ok):
                hint = "جارٍ تشغيل الكاميرا…"
            elif self.activity < 0.1:
                hint = {"idle": self.page["invite"], "presence": PRESENCE_HINT}.get(self.state)
            elif not self._holds and self.transition is None:
                since = self.t - getattr(self, "_hint_t0", self.t)
                hint = self.page["grab_hint"] if since < 5.0 else (GESTURE_HINT if since < 11.0 else None)
            if hint:
                s = self.ui.render(hint, 18, 300, self.pal["dim"])
                pulse = 0.55 + 0.45 * math.sin(self.t * 2.0)
                fade = 1.0 if self.state == "active" else max(0.0, 1.0 - self.activity * 8)
                s.set_alpha(int(255 * pulse * fade))
                self.screen.blit(s, s.get_rect(midbottom=(W // 2, H - 34)))

    def _draw_debug(self):
        W, H = self.size
        pad = 20
        x0, y0, bw = W - pad - 220, H - pad - 130, 220
        pygame.draw.rect(self.screen, self.pal["bg"], (x0 - 160, y0 - 6, bw + 160 + pad, 130 + pad))
        track = self._track
        opens = "  ".join(f"{hf.open:.2f}/{hf.grip:.2f}" for hf in self.hand_filters.values())
        lines = [
            f"القرب: {self.prox_smooth:.2f}  (الحد {self.near_threshold:.2f})",
            f"النشاط: {self.activity:.2f}   الوزن: {int(self.global_weight)}",
            f"اليدان: {len(self.hand_screen)}  انفتاح/إمساك: {opens}" if track else "",
            f"كاميرا {track.cam_fps:.0f} · يد {track.fps:.0f} ({'بعيد' if track.far else 'قريب'}) · جسم {track.pose_fps:.0f} إطار/ث" if track else "",
            f"إطارات الرسم: {self.clock.get_fps():.0f} · عمر القراءة: {track.latency_ms:.0f} ms" if track else "",
        ]
        for i, ln in enumerate(lines):
            if ln:
                s = self.ui.render(ln, 14, 400, (200, 192, 180))
                self.screen.blit(s, s.get_rect(topright=(W - pad, y0 + i * 20)))
        # مقياس القرب
        by = y0 + 106
        pygame.draw.rect(self.screen, (60, 56, 52), (x0, by, bw, 8))
        fill = int(bw * min(1.0, self.prox_smooth / 0.5))
        pygame.draw.rect(self.screen, self.pal["accent"], (x0 + bw - fill, by, fill, 8))
        tx = x0 + bw - int(bw * self.near_threshold / 0.5)
        pygame.draw.line(self.screen, self.pal["ink"], (tx, by - 4), (tx, by + 12), 2)

    def _draw_camera(self):
        track = self._track
        if track is None or track.frame is None:
            return
        fr = track.frame
        cached = getattr(self, "_camera_surface", None)
        if cached is None or cached[0] is not fr:
            cached = (fr, pygame.surfarray.make_surface(np.ascontiguousarray(fr.swapaxes(0, 1))))
            self._camera_surface = cached
        surf = cached[1]
        h = self.size[1]
        self.screen.blit(surf, (20, h - 20 - surf.get_height()))
        pygame.draw.rect(self.screen, (90, 84, 78), (20, h - 20 - surf.get_height(),
                                                    surf.get_width(), surf.get_height()), 1)

    def _draw_recording_badge(self):
        if not self.writer:
            return
        if int(self.t * 2) % 2 == 0:
            pygame.draw.circle(self.screen, (220, 50, 50), (28, 28), 8)
        s = self.ui.render("تسجيل", 14, 600, (220, 80, 80))
        self.screen.blit(s, s.get_rect(midleft=(44, 28)))

