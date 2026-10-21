"""
فيديو شرح جدار التيبوغرافيا: العلبة (ملفا STL الحقيقيان)، تركيب القطع، التعليق على الحائط، جهاز العرض،
ثم التطبيق خطوة بخطوة بلقطات حقيقية منه، مع تعليق صوتي عربي وترجمة وموسيقى الخلفية.

    python tools/video/make_video.py <work> <out.mp4> [--preview t1 t2 ...]

<work>: فيه case/*.stl و voice/<scene>.mp3 و app/ (من capture_app.py)
الإطارات تُرسل مباشرة إلى ffmpeg (لا تُخزَّن في الذاكرة).
"""
import json
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import parts as P  # noqa: E402
from narration import SCENES  # noqa: E402
from render3d import Mesh, draw_meshes, load_stl, rot  # noqa: E402

W, H, FPS = 1280, 720, 25
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
BG = (30, 24, 20)
CREAM = (242, 230, 208)
DIM = (190, 172, 150)
ACCENT = (226, 120, 72)
CLAY = (165, 119, 79)
FONT = os.path.join(ROOT, "assets", "fonts", "NotoSansArabic.ttf")

# ----------------------------------------------------------------------
# أدوات
# ----------------------------------------------------------------------
_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        f = ImageFont.truetype(FONT, size)
        try:
            f.set_variation_by_axes([700 if bold else 450])
        except Exception:
            pass
        _fonts[key] = f
    return _fonts[key]


def ar(s):
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(s))


def text(dr, xy, s, size, color=CREAM, bold=False, anchor="mm", alpha=255):
    col = tuple(color[:3]) + (int(alpha),)
    dr.text(xy, ar(s), font=font(size, bold), fill=col, anchor=anchor)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def seg(t, a, b):
    """تقدّم 0..1 بين الزمنين a و b (نسبة من المشهد)."""
    return ease((t - a) / max(b - a, 1e-6))


def lerp(a, b, t):
    return a + (b - a) * t


_bg_cache = {}


def backdrop(kind="studio"):
    if kind not in _bg_cache:
        img = Image.new("RGB", (W, H), BG)
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.hypot((xx - W / 2) / (W * 0.62), (yy - H * 0.45) / (H * 0.75))
        base = np.array(BG, float)
        glow = np.array((70, 52, 40), float)
        arr = base + (glow - base) * np.clip(1 - d, 0, 1)[..., None] ** 1.6
        img = Image.fromarray(arr.astype(np.uint8))
        _bg_cache[kind] = img
    return _bg_cache[kind].copy().convert("RGBA")


def shadow(img, cx, cy, rx, ry, alpha=110):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(0, 0, 0, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(18)))


def label(img, anchor_xy, box_xy, title, sub="", alpha=1.0):
    if alpha <= 0.01:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    a = int(255 * alpha)
    ax, ay = anchor_xy
    bx, by = box_xy
    dr.line([(ax, ay), (bx, by)], fill=ACCENT + (a,), width=3)
    dr.ellipse([ax - 7, ay - 7, ax + 7, ay + 7], outline=ACCENT + (a,), width=3)
    dr.ellipse([ax - 3, ay - 3, ax + 3, ay + 3], fill=ACCENT + (a,))
    tw = max(dr.textlength(ar(title), font=font(26, True)), dr.textlength(ar(sub), font=font(19)) if sub else 0)
    h = 74 if sub else 46
    left = min(max(bx - (tw + 34) / 2, 16), W - 16 - (tw + 34))
    dr.rounded_rectangle([left, by - h / 2, left + tw + 34, by + h / 2], 12,
                         fill=(24, 19, 16, int(225 * alpha)), outline=ACCENT + (a,), width=2)
    cx = left + (tw + 34) / 2
    if sub:
        text(dr, (cx, by - 14), title, 26, CREAM, True, alpha=a)
        text(dr, (cx, by + 18), sub, 19, DIM, alpha=a)
    else:
        text(dr, (cx, by), title, 26, CREAM, True, alpha=a)
    img.alpha_composite(layer)


def heading(img, s, t_in):
    a = 255 * ease(t_in * 3)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    text(ImageDraw.Draw(layer), (W - 60, 52), s, 34, CREAM, True, anchor="rm", alpha=a)
    img.alpha_composite(layer)


# ----------------------------------------------------------------------
# المشهد ثلاثي الأبعاد
# ----------------------------------------------------------------------
class Case3D:
    def __init__(self, case_dir):
        self.base = Mesh(load_stl(os.path.join(case_dir, "esp32cam_base.stl")), (196, 138, 92))
        self.lid = Mesh(load_stl(os.path.join(case_dir, "esp32cam_lid.stl")), (172, 118, 78))
        self.bb = P.breadboard()
        self.pm = P.power_module()
        self.esp = P.esp32cam()
        self.center = np.array([35.0, 45.0, 14.0])

    def project(self, pt, view, scale, center, offset=(0, 0, 0)):
        p = (np.asarray(pt, float) + np.asarray(offset, float) - self.center) @ view.T
        return center[0] + p[0] * scale, center[1] - p[1] * scale, p[2]

    def render(self, img, view, scale, center, lid_dz=0.0, lid_alpha=1.0, base_alpha=1.0,
               bb=None, pm=None, esp=None, extra=()):
        """bb/pm/esp: إزاحة z (مم) لكل قطعة، أو None لإخفائها. extra: [Mesh] بإحداثيات القاعدة."""
        I = np.eye(3)
        c = -self.center
        items = []
        if base_alpha > 0:
            items.append((self.base, I, c, base_alpha))
        for meshes, dz in ((self.bb, bb), (self.pm, pm), (self.esp, esp)):
            if dz is not None:
                items += [(m, I, c + [0, 0, dz], 1.0) for m in meshes]
        for m in extra:
            items.append((m, I, c, 1.0))
        if lid_alpha > 0:
            items.append((self.lid, I, c + [0, 0, lid_dz], lid_alpha))
        draw_meshes(img, items, view, scale, center)


# ----------------------------------------------------------------------
# المشاهد
# ----------------------------------------------------------------------
class Video:
    def __init__(self, work):
        self.work = work
        self.case = Case3D(os.path.join(work, "case"))
        self.app_dir = os.path.join(work, "app")
        self.icon = Image.open(os.path.join(ROOT, "app_icon.png")).convert("RGBA")
        self.splash = Image.open(os.path.join(ROOT, "splash.png")).convert("RGBA")
        self.launch = {k: Image.open(os.path.join(self.app_dir, f"launcher_{k}.png")).convert("RGBA")
                       for k in ("esp_off", "esp_settling", "esp_ok", "usb_ok")}
        self.clips = {}
        for name in ("play", "slides"):
            d = os.path.join(self.app_dir, "clip_" + name)
            n = len([f for f in os.listdir(d) if f.endswith(".jpg")])
            self.clips[name] = (d, n, json.load(open(os.path.join(d, "hand.json"))))
        self._case_front = None
        self._frame_cache = {}

    # --- لقطات التطبيق ---
    def clip_frame(self, name, i):
        d, n, _ = self.clips[name]
        i = int(min(max(i, 0), n - 1))
        key = (name, i)
        if key not in self._frame_cache:
            if len(self._frame_cache) > 8:
                self._frame_cache.clear()
            self._frame_cache[key] = Image.open(os.path.join(d, f"{i:05d}.jpg")).convert("RGBA")
        return self._frame_cache[key]

    def case_front(self):
        """العلبة مركّبة من الأمام (الغطاء نحو المشاهد، الأعلى للأعلى) لمشاهد الحائط."""
        if self._case_front is None:
            img = Image.new("RGBA", (190, 300), (0, 0, 0, 0))
            self.case.render(img, rot(0, 0), 2.05, (95, 150))
            self._case_front = img
        return self._case_front

    # --- 1. المقدمة ---
    def s_intro(self, t, dur):
        img = backdrop()
        k = seg(t, 0.0, 0.25)
        size = int(lerp(60, 210, k))
        ic = self.icon.resize((size, size), Image.LANCZOS)
        ic.putalpha(Image.eval(ic.getchannel("A"), lambda v: int(v * k)))
        img.alpha_composite(ic, (W // 2 - size // 2, int(250 - size / 2)))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        a1 = 255 * seg(t, 0.15, 0.35)
        text(dr, (W // 2, 430), "جدار التيبوغرافيا التفاعلية", 64, CREAM, True, alpha=a1)
        a2 = 255 * seg(t, 0.3, 0.5)
        text(dr, (W // 2, 500), "العلبة · التركيب · الحائط · التطبيق", 30, ACCENT, alpha=a2)
        img.alpha_composite(layer)
        return img

    # --- 2. العلبة ---
    def s_case(self, t, dur):
        img = backdrop()
        yaw = lerp(-25, 40, t) + 8 * math.sin(t * 6)
        pitch = -58 + 6 * math.sin(t * 4)
        view = rot(yaw, pitch)
        scale = 4.4
        ctr = (W * 0.46, H * 0.52)
        shadow(img, ctr[0], ctr[1] + 150, 230, 40)
        self.case.render(img, view, scale, ctr)
        heading(img, "علبة الكاميرا: لوح طيني رافديني", t * dur / 4)
        steps = [("inscription", 0.10, "نقشان مسماريان", "ملك سومر · السماء والأرض"),
                 ("lens", 0.40, "فتحة العدسة", "16 مم"),
                 ("button", 0.50, "فتحة زر التشغيل", "13 × 13 مم"),
                 ("vents", 0.60, "فتحات تهوية", "على الجانبين"),
                 ("openings", 0.70, "مخرجا الكهرباء والـ USB", "في الجدار السفلي"),
                 ("ear_bottom", 0.84, "أذنا التعليق", "مسماران على الحائط")]
        boxes = {"inscription": (W - 300, 180), "lens": (W - 280, 270), "button": (210, 560),
                 "vents": (W - 260, 470), "openings": (230, 470), "ear_bottom": (230, 610)}
        for key, start, title, sub in steps:
            if t < start:
                continue
            ax, ay, _ = self.case.project(P.ANCHORS[key], view, scale, ctr)
            alpha = seg(t, start, start + 0.05) * (1 - seg(t, start + 0.22, start + 0.27) if key != "ear_bottom" else 1)
            label(img, (ax, ay), boxes[key], title, sub, alpha)
        return img

    # --- 3. القطع ---
    def s_parts(self, t, dur):
        img = backdrop()
        view = rot(lerp(-35, 15, t), -55)
        scale = 4.6
        ctr = (W * 0.5, H * 0.56)
        shadow(img, ctr[0], ctr[1] + 140, 230, 38)
        drop = lambda a, b: None if t < a else 120 * (1 - seg(t, a, b))  # noqa: E731
        bb, pm, esp = drop(0.05, 0.25), drop(0.35, 0.55), drop(0.65, 0.85)
        self.case.render(img, view, scale, ctr, base_alpha=0, lid_alpha=0, bb=bb, pm=pm, esp=esp)
        heading(img, "ثلاث قطع داخل العلبة", t * dur / 4)
        for key, start, title, sub, box_xy in (
                ("breadboard", 0.18, "لوح التجارب (بريدبورد)", "55 × 82.5 مم", (220, 230)),
                ("power", 0.48, "موزّع الطاقة MB102", "على الطرف السفلي", (220, 520)),
                ("esp", 0.78, "كاميرا ESP32-CAM", "في منتصف اللوح", (W - 230, 250))):
            if t >= start:
                dz = {"breadboard": bb, "power": pm, "esp": esp}[key] or 0
                ax, ay, _ = self.case.project(P.ANCHORS[key], view, scale, ctr, (0, 0, dz))
                label(img, (ax, ay), box_xy, title, sub, seg(t, start, start + 0.06))
        return img

    # --- 4. التركيب ---
    def s_assembly(self, t, dur):
        img = backdrop()
        view = rot(lerp(10, 35, t), lerp(-62, -50, t))
        scale = 4.2
        ctr = (W * 0.5, H * 0.55)
        shadow(img, ctr[0], ctr[1] + 150, 240, 40)
        stack = 70 * (1 - seg(t, 0.02, 0.22))
        lid = 80 * (1 - seg(t, 0.25, 0.42)) if t >= 0.22 else None
        extra = []
        for i, (x, y) in enumerate(P.SCREW_HOLES):
            a = 0.45 + i * 0.05
            if t >= a:
                k = seg(t, a, a + 0.12)
                extra += P.screw(x, y, P.SCREW_TOP + 30 * (1 - min(1, k * 1.6)) - 3 * k, turn=k * 12)
        plug_k = seg(t, 0.74, 0.88)
        if t >= 0.72:
            extra += P.dc_plug(-70 + 70 * plug_k)
        self.case.render(img, view, scale, ctr, lid_dz=lid if lid is not None else 0,
                         lid_alpha=0 if lid is None else 1, bb=stack, pm=stack, esp=stack, extra=extra)
        heading(img, "التركيب", t * dur / 4)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        if 0.45 <= t < 0.74:
            text(dr, (W // 2, 120), "أربعة براغٍ M3", 34, ACCENT, True, alpha=255 * seg(t, 0.45, 0.5))
        if t >= 0.9:
            bx, by, _ = self.case.project(P.ANCHORS["button"], view, scale, ctr)
            r = 14 + 40 * ((t - 0.9) * 10 % 1)
            dr.ellipse([bx - r, by - r, bx + r, by + r], outline=(255, 220, 120, int(255 * (1 - (t - 0.9) * 10 % 1))), width=4)
            text(dr, (W // 2, 120), "تشغيل", 34, ACCENT, True)
        img.alpha_composite(layer)
        return img

    # --- مشهد الغرفة ---
    def room(self, wall_case=True, case_alpha=1.0):
        img = Image.new("RGBA", (W, H), (226, 216, 198, 255))
        dr = ImageDraw.Draw(img)
        for y in range(0, 560):                       # تدرّج الحائط
            v = int(10 * y / 560)
            dr.line([(0, y), (W, y)], fill=(230 - v, 220 - v, 203 - v, 255))
        dr.rectangle([0, 560, W, H], fill=(150, 118, 88, 255))
        dr.rectangle([0, 548, W, 562], fill=(196, 182, 160, 255))
        for x in range(-200, W, 160):                  # ألواح الأرضية
            dr.line([(x, H), (x + 120, 562)], fill=(138, 108, 80, 255), width=2)
        if wall_case:
            self.put_case(img, 640, 300, case_alpha)
        return img

    def put_case(self, img, cx, cy, alpha=1.0, scale=0.42):
        c = self.case_front()
        w, h = int(c.width * scale), int(c.height * scale)
        cs = c.resize((w, h), Image.LANCZOS)
        if alpha < 1:
            cs.putalpha(Image.eval(cs.getchannel("A"), lambda v: int(v * alpha)))
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([cx - w * 0.36 + 5, cy - h * 0.3 + 6, cx + w * 0.36 + 5, cy + h * 0.32 + 6],
                                             8, fill=(0, 0, 0, int(70 * alpha)))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
        img.alpha_composite(cs, (int(cx - w / 2), int(cy - h / 2)))

    def person(self, img, x, floor, s=1.0, walk=0.0, arm_l=(-80, 10), arm_r=(-80, 10), facing=1, reach_r=None):
        """شخص مسطّح بسيط. arm_*: (زاوية العضد، زاوية الساعد) بالدرجات من الأفق (−90 للأسفل)."""
        dr = ImageDraw.Draw(img)
        skin, shirt, pants, hair = (214, 170, 132, 255), (72, 100, 124, 255), (52, 52, 62, 255), (40, 30, 26, 255)
        hip_y = floor - 190 * s
        sh_y = hip_y - 150 * s
        sw = 4 * math.sin(walk * 2 * math.pi)
        for sgn in (-1, 1):                            # الساقان
            ang = math.radians(sgn * 14 * math.sin(walk * 2 * math.pi))
            kx, ky = x + sgn * 14 * s + math.sin(ang) * 95 * s, hip_y + math.cos(ang) * 95 * s
            fx, fy = kx + math.sin(ang * 0.4) * 95 * s, floor
            dr.line([(x + sgn * 14 * s, hip_y), (kx, ky), (fx, fy)], fill=pants, width=int(30 * s), joint="curve")
            dr.ellipse([fx - 18 * s + facing * 8 * s, fy - 12 * s, fx + 22 * s + facing * 8 * s, fy + 4 * s], fill=(35, 30, 28, 255))
        dr.rounded_rectangle([x - 42 * s, sh_y - 6 * s + sw, x + 42 * s, hip_y + 10 * s], int(26 * s), fill=shirt)

        def arm(side, a1, a2):
            sx = x + side * 36 * s
            r1, r2 = math.radians(a1), math.radians(a2)
            ex, ey = sx + math.cos(r1) * 78 * s * side, sh_y + 10 * s - math.sin(r1) * 78 * s
            hx, hy = ex + math.cos(r2) * 70 * s * side, ey - math.sin(r2) * 70 * s
            dr.line([(sx, sh_y + 10 * s), (ex, ey), (hx, hy)], fill=shirt, width=int(24 * s), joint="curve")
            dr.ellipse([hx - 12 * s, hy - 12 * s, hx + 12 * s, hy + 12 * s], fill=skin)
            return hx, hy
        hl = arm(-1, *arm_l)
        if reach_r is not None:                         # ذراع ممدودة نحو نقطة (العلبة)
            sx, sy = x + 36 * s, sh_y + 10 * s
            ang = math.degrees(math.atan2(-(reach_r[1] - sy), reach_r[0] - sx))
            arm_r = (ang - 12, ang + 12)
        hr = arm(1, *arm_r)
        dr.rectangle([x - 9 * s, sh_y - 30 * s, x + 9 * s, sh_y], fill=skin)
        hx, hy = x, sh_y - 62 * s + sw
        dr.ellipse([hx - 34 * s, hy - 38 * s, hx + 34 * s, hy + 38 * s], fill=skin)
        dr.chord([hx - 36 * s, hy - 42 * s, hx + 36 * s, hy + 20 * s], 180, 360, fill=hair)
        return hl, hr

    # --- 5. الحائط ---
    def s_wall(self, t, dur):
        img = self.room(wall_case=t > 0.62)
        mount = (640, 300)
        if t < 0.30:                                   # يمشي حاملاً العلبة
            k = seg(t, 0.0, 0.3)
            px = lerp(-120, 500, k)
            self.person(img, px, 650, 1.0, walk=k * 4, arm_l=(-80, -80), reach_r=(px + 95, 410))
            self.put_case(img, px + 95, 395, 1.0, 0.36)
        elif t < 0.62:                                 # يرفعها ويثبّتها
            k = seg(t, 0.3, 0.45)
            px = 500
            cx, cy = lerp(px + 95, mount[0], k), lerp(395, mount[1], k)
            self.put_case(img, cx, cy, 1.0, lerp(0.36, 0.42, k))
            self.person(img, px, 650, 1.0, arm_l=(-80, -80), reach_r=(cx - 18, cy + 10))
            if t > 0.45:                               # مفك ومسماران
                dr = ImageDraw.Draw(img)
                for i, ey in enumerate((mount[1] - 66, mount[1] + 66)):
                    a = 0.45 + i * 0.08
                    if t > a:
                        kk = seg(t, a, a + 0.08)
                        r = 10 + 26 * kk
                        dr.ellipse([mount[0] - r, ey - r, mount[0] + r, ey + r],
                                   outline=(226, 120, 72, int(255 * (1 - kk))), width=4)
        else:                                          # يرجع خطوة
            k = seg(t, 0.62, 0.8)
            self.person(img, lerp(500, 330, k), 650, 1.0, walk=k * 2, arm_l=(-80, -80), arm_r=(-80, -80))
            if t > 0.8:
                dr = ImageDraw.Draw(img)
                y0, y1 = mount[1] + 40, 560
                dr.line([(760, y0), (760, y1)], fill=(120, 90, 70, 255), width=3)
                for yy in (y0, y1):
                    dr.line([(748, yy), (772, yy)], fill=(120, 90, 70, 255), width=3)
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                text(ImageDraw.Draw(layer), (780, (y0 + y1) / 2), "نحو مستوى الصدر أو أعلى قليلاً", 24,
                     (90, 60, 40), True, anchor="lm", alpha=255 * seg(t, 0.8, 0.86))
                img.alpha_composite(layer)
        heading(img, "التعليق على الحائط", t * dur / 4)
        return img

    # --- 6. جهاز العرض ---
    def s_projector(self, t, dur):
        img = self.room(wall_case=False)
        rect = (330, 110, 950, 460)
        k = seg(t, 0.35, 0.55)
        if k > 0:
            frame = self.clip_frame("play", 60).resize((rect[2] - rect[0], rect[3] - rect[1]), Image.LANCZOS)
            frame.putalpha(int(235 * k))
            img.alpha_composite(frame, (rect[0], rect[1]))
        self.put_case(img, 640, 300)
        dr = ImageDraw.Draw(img)
        # طاولة وحاسوب وجهاز عرض
        dr.rectangle([860, 560, 1240, 590], fill=(110, 80, 60, 255))
        dr.rectangle([880, 590, 900, 700], fill=(90, 64, 48, 255))
        dr.rectangle([1200, 590, 1220, 700], fill=(90, 64, 48, 255))
        dr.polygon([(900, 560), (1040, 560), (1060, 548), (920, 548)], fill=(60, 60, 66, 255))
        dr.polygon([(920, 548), (1060, 548), (1045, 450), (930, 450)], fill=(50, 50, 56, 255))
        scr = self.launch["esp_ok"].resize((96, 88)) if t < 0.6 else self.clip_frame("play", 60).resize((100, 86))
        img.alpha_composite(scr, (938, 456))
        pj = (1120, 520)
        dr.rounded_rectangle([pj[0] - 60, pj[1] - 28, pj[0] + 60, pj[1] + 40], 10, fill=(230, 230, 232, 255))
        dr.ellipse([pj[0] - 52, pj[1] - 12, pj[0] - 20, pj[1] + 20], fill=(40, 40, 46, 255))
        # كابل HDMI
        hk = seg(t, 0.05, 0.3)
        pts = [(1055, 552), (1070, 575), (1090, 585), (1110, 572), (1120, 560)]
        n = max(2, int(len(pts) * hk) + 1)
        if hk > 0:
            dr.line(pts[:n], fill=(25, 25, 28, 255), width=6, joint="curve")
        if hk > 0.5:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            text(ImageDraw.Draw(layer), (1080, 630), "HDMI", 26, (60, 40, 30), True, alpha=255 * hk)
            img.alpha_composite(layer)
        if k > 0:                                      # شعاع الضوء
            beam = Image.new("RGBA", img.size, (0, 0, 0, 0))
            lens = (pj[0] - 36, pj[1] + 4)
            ImageDraw.Draw(beam).polygon([lens, (rect[0], rect[1]), (rect[2], rect[1]), (rect[2], rect[3]), (rect[0], rect[3])],
                                         fill=(255, 245, 210, int(40 * k)))
            img.alpha_composite(beam.filter(ImageFilter.GaussianBlur(6)))
        if t > 0.7:                                    # شخص يرفع يده أمام الحائط
            kk = seg(t, 0.7, 0.85)
            self.person(img, 300, 690, 0.9, arm_l=(-80, -80), arm_r=(lerp(-80, 50, kk), lerp(-80, 80, kk)))
        heading(img, "جهاز العرض على الحائط نفسه", t * dur / 4)
        return img

    # --- 7. نافذة البدء ---
    def s_launcher(self, t, dur):
        img = Image.new("RGBA", (W, H), (70, 52, 40, 255))
        img.alpha_composite(backdrop())
        dr = ImageDraw.Draw(img)
        # سطح المكتب: الأيقونة ثم النقر المزدوج
        if t < 0.16:
            ic = self.icon.resize((96, 96), Image.LANCZOS)
            img.alpha_composite(ic, (140, 140))
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            text(ImageDraw.Draw(layer), (188, 262), "Typography Wall", 20, CREAM)
            img.alpha_composite(layer)
            cur = (lerp(700, 200, seg(t, 0, 0.08)), lerp(500, 190, seg(t, 0, 0.08)))
            if 0.09 < t < 0.13:
                rr = 20 + 300 * (t - 0.09)
                dr.ellipse([cur[0] - rr, cur[1] - rr, cur[0] + rr, cur[1] + rr], outline=ACCENT + (200,), width=3)
            self.cursor(img, cur)
            if t > 0.13:
                sp = self.splash.copy()
                sp.putalpha(int(255 * seg(t, 0.13, 0.16)))
                img.alpha_composite(sp, (W // 2 - sp.width // 2, H // 2 - sp.height // 2))
            return self.caption_steps(img, t)
        # النافذة: المراحل
        if t < 0.30:
            key = "esp_off"
        elif t < 0.46:
            key = "esp_settling"
        elif t < 0.64:
            key = "esp_ok"
        elif t < 0.82:
            key = "usb_ok"
        else:
            key = "esp_ok"
        win = self.launch[key]
        sc = 590 / win.height
        ww, wh = int(win.width * sc), int(win.height * sc)
        ox, oy = W // 2 - ww // 2, 24
        img.alpha_composite(win.resize((ww, wh), Image.LANCZOS), (ox, oy))
        L = lambda x, y: (ox + x * sc, oy + y * sc)  # noqa: E731
        targets = [(0.16, 0.26, L(277, 128)), (0.30, 0.36, L(149, 340)), (0.64, 0.72, L(445, 128)),
                   (0.82, 0.86, L(277, 128)), (0.86, 0.90, L(585, 634)), (0.92, 0.97, L(360, 687))]
        cur = L(600, 400)
        for a, b, p in targets:
            if t >= a:
                cur = (lerp(cur[0], p[0], seg(t, a, b)), lerp(cur[1], p[1], seg(t, a, b)))
        for a, b, p in targets:
            if b <= t < b + 0.03:
                rr = 12 + 900 * (t - b)
                dr.ellipse([p[0] - rr, p[1] - rr, p[0] + rr, p[1] + rr], outline=ACCENT + (220,), width=3)
        self.cursor(img, cur)
        return self.caption_steps(img, t)

    def caption_steps(self, img, t):
        steps = [(0.0, "١. افتح التطبيق"), (0.16, "٢. اختر ESP واي فاي"), (0.30, "٣. اتصل تلقائياً"),
                 (0.46, "٤. انتظر الأخضر"), (0.64, "أو: ESP بالكابل"), (0.82, "٥. ملء الشاشة ثم ابدأ")]
        cur = [s for a, s in steps if t >= a][-1]
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        dr.rounded_rectangle([W - 330, 40, W - 40, 100], 14, fill=(24, 19, 16, 230), outline=ACCENT + (255,), width=2)
        text(dr, (W - 185, 70), cur, 28, CREAM, True)
        img.alpha_composite(layer)
        return img

    def cursor(self, img, p):
        x, y = p
        pts = [(x, y), (x, y + 30), (x + 8, y + 23), (x + 14, y + 36), (x + 20, y + 33), (x + 14, y + 20), (x + 24, y + 20)]
        dr = ImageDraw.Draw(img)
        dr.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))

    # --- 8/9. العرض الحي ---
    def s_clip(self, name, t, dur, title):
        d, n, hands = self.clips[name]
        i = min(n - 1, int(t * n))
        img = self.clip_frame(name, i).copy()
        hx, hy, grip = hands[i]
        self.hand(img, hx * W, hy * H, grip)
        heading_bg = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(heading_bg).rounded_rectangle([W - 470, 24, W - 30, 82], 14, fill=(24, 19, 16, 200))
        img.alpha_composite(heading_bg)
        heading(img, title, 1.0)
        return img

    def hand(self, img, x, y, grip):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        skin, edge = (238, 205, 170, 235), (110, 70, 45, 255)
        y += 60                                         # المؤشر عند الأصابع؛ الكف تحته
        if grip:
            dr.rounded_rectangle([x - 34, y - 70, x + 34, y - 10], 20, fill=skin, outline=edge, width=3)
            for k in range(4):
                dr.line([(x - 26 + k * 17, y - 70), (x - 26 + k * 17, y - 52)], fill=edge, width=2)
        else:
            dr.rounded_rectangle([x - 32, y - 52, x + 32, y + 8], 18, fill=skin, outline=edge, width=3)
            for k, (dx, ln) in enumerate(((-24, 50), (-8, 62), (8, 60), (24, 48))):
                dr.rounded_rectangle([x + dx - 7, y - 52 - ln, x + dx + 7, y - 44], 7, fill=skin, outline=edge, width=3)
            dr.rounded_rectangle([x + 28, y - 40, x + 64, y - 26], 7, fill=skin, outline=edge, width=3)
        img.alpha_composite(layer)

    # --- 10. الختام ---
    def s_outro(self, t, dur):
        img = backdrop()
        view = rot(t * 140 - 30, -55)
        ctr = (W * 0.27, H * 0.5)
        shadow(img, ctr[0], ctr[1] + 120, 170, 30)
        self.case.render(img, view, 3.2, ctr)
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        tips = [("C", "صورة الكاميرا"), ("-  =", "حساسية حركة اليد"), ("A", "الدليل الصوتي"), ("D", "معلومات الأداء")]
        for i, (k, s) in enumerate(tips):
            a = 255 * seg(t, 0.05 + i * 0.07, 0.12 + i * 0.07) * (1 - seg(t, 0.62, 0.7))
            y = 180 + i * 78
            dr.rounded_rectangle([W - 200, y - 26, W - 90, y + 26], 12, outline=ACCENT + (int(a),), width=3)
            dr.text((W - 145, y), k, font=font(28, True), fill=CREAM + (int(a),), anchor="mm")
            text(dr, (W - 230, y), s, 30, CREAM, anchor="rm", alpha=a)
        a2 = 255 * seg(t, 0.7, 0.8)
        text(dr, (W * 0.64, 330), "جدار التيبوغرافيا التفاعلية", 54, CREAM, True, alpha=a2)
        text(dr, (W * 0.64, 400), "كاميرا في لوح طيني · حروف تحيا بين أيديكم", 28, ACCENT, alpha=a2)
        img.alpha_composite(layer)
        return img

    # ------------------------------------------------------------------
    def scene(self, key, t, dur):
        if key == "play":
            return self.s_clip("play", t, dur, "حرّك يدك أمام الحائط")
        if key == "slides":
            return self.s_clip("slides", t, dur, "الشرائح: عربي · مسماري · صورة")
        return getattr(self, "s_" + key)(t, dur)


# ----------------------------------------------------------------------
# الصوت والترجمة
# ----------------------------------------------------------------------
SR = 44100


def decode_mp3(path):
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16)


def subtitle_chunks(textv, dur):
    """يقسّم النص إلى جمل قصيرة بأزمنة تقريبية بحسب طولها."""
    import re
    parts = [p.strip() for p in re.split(r"(?<=[\.،:؟!])\s+", textv) if p.strip()]
    chunks = []
    for p in parts:
        words = p.split()
        while len(words) > 12:                       # سطر واحد مقروء
            chunks.append(" ".join(words[:10]))
            words = words[10:]
        chunks.append(" ".join(words))
    total = sum(len(c) for c in chunks)
    out, t = [], 0.0
    for c in chunks:
        d = dur * len(c) / total
        out.append((t, t + d, c))
        t += d
    return out


def draw_subtitle(img, s):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(layer)
    f = font(30)
    tw = dr.textlength(ar(s), font=f)
    dr.rounded_rectangle([W / 2 - tw / 2 - 22, H - 78, W / 2 + tw / 2 + 22, H - 26], 12, fill=(0, 0, 0, 170))
    dr.text((W / 2, H - 52), ar(s), font=f, fill=(255, 250, 240, 255), anchor="mm")
    img.alpha_composite(layer)


def build(work, out, preview=None):
    v = Video(work)
    voice_dir = os.path.join(work, "voice")
    lead, tail = 0.5, 1.0
    plan, t0 = [], 0.0
    for key, textv in SCENES:
        pcm = decode_mp3(os.path.join(voice_dir, key + ".mp3"))
        vd = len(pcm) / SR
        dur = lead + vd + tail
        plan.append((key, t0, dur, pcm, vd, subtitle_chunks(textv, vd)))
        t0 += dur
    total = t0
    print(f"total {total:.1f} s, {int(total * FPS)} frames", flush=True)
    if preview:
        os.makedirs(os.path.join(work, "preview"), exist_ok=True)
        for ts in preview:
            for key, st, dur, _, vd, subs in plan:
                if st <= ts < st + dur:
                    img = v.scene(key, (ts - st) / dur, dur)
                    tt = ts - st - lead
                    for a, b, s in subs:
                        if a <= tt < b:
                            draw_subtitle(img, s)
                    p = os.path.join(work, "preview", f"t{ts:06.1f}_{key}.jpg")
                    img.convert("RGB").save(p, quality=88)
                    print("  ", p, flush=True)
        return
    # مسار الصوت: التعليق في أماكنه + الموسيقى بصوت منخفض (يمزجها ffmpeg)
    track = np.zeros(int(total * SR) + SR, np.int16)
    for key, st, dur, pcm, vd, subs in plan:
        s = int((st + lead) * SR)
        track[s:s + len(pcm)] = pcm
    voice_wav = os.path.join(work, "voice_track.wav")
    with wave.open(voice_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(track.tobytes())
    # 1) الإطارات صوراً على القرص (يستأنف من حيث توقف إن انقطع؛ ذاكرة الجهاز قليلة)
    frames = os.path.join(work, "frames")
    os.makedirs(frames, exist_ok=True)
    n = int(total * FPS)
    for i in range(n):
        path = os.path.join(frames, f"{i:05d}.jpg")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        ts = i / FPS
        for key, st, dur, _, vd, subs in plan:
            if st <= ts < st + dur:
                img = v.scene(key, (ts - st) / dur, dur)
                tt = ts - st - lead
                for a, b, s in subs:
                    if a <= tt < b:
                        draw_subtitle(img, s)
                break
        edge = min(ts - st, st + dur - ts)                 # تلاشٍ قصير بين المشاهد
        rgb = img.convert("RGB")
        del img
        if edge < 0.25:
            rgb = Image.blend(Image.new("RGB", rgb.size, (0, 0, 0)), rgb, max(edge / 0.25, 0))
        rgb.save(path + ".tmp", "JPEG", quality=92)
        os.replace(path + ".tmp", path)
        if i % 250 == 0:
            print(f"  frame {i}/{n} ({key})", flush=True)
    # 2) الترميز: الصور + التعليق + الموسيقى بصوت منخفض
    music = os.path.join(ROOT, "assets", "audio", "music", "ambient.wav")
    cmd = [FFMPEG, "-y", "-v", "error", "-framerate", str(FPS), "-i", os.path.join(frames, "%05d.jpg"),
           "-i", voice_wav, "-stream_loop", "-1", "-i", music,
           "-filter_complex", "[2:a]volume=0.16[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]",
           "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-threads", "2", "-crf", "21",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True)
    print("done:", out, flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--preview" in args:
        k = args.index("--preview")
        build(args[0], args[1], [float(x) for x in args[k + 1:]])
    else:
        build(args[0], args[1])
