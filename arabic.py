"""
معالجة النص العربي ورسم الحروف بالخط المتغيّر.

- تشكيل الحروف (وصلها حسب موضعها) عبر arabic_reshaper
- ترتيب الاتجاه من اليمين لليسار عبر python-bidi
- رسم كل حرف بوزن متغيّر (Variable Font) عبر Pillow ثم تحويله لسطح pygame
- سلسلة خطوط احتياطية: إن خلا الخط الأساسي من حرف (لاتيني، رقم، رمز) يُؤخذ من الخط التالي
"""

import math
import os
import unicodedata

import arabic_reshaper
import pygame
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from . import config

_reshaper = arabic_reshaper.ArabicReshaper(
    configuration={"delete_harakat": True, "support_ligatures": True}
)


def shape(text: str) -> str:
    """يعيد النص بأشكال الحروف الموصولة وبترتيب العرض البصري (من اليسار لليمين)."""
    return get_display(_reshaper.reshape(text))


class VariableFont:
    """خط واحد (متغيّر إن أمكن) مع ذاكرة مؤقتة لكل (حجم، وزن)."""

    def __init__(self, path: str):
        self.path = path
        self._fonts = {}
        # shape() already performs bidi ordering; RAQM would reorder it again.
        probe = ImageFont.truetype(path, 40, layout_engine=ImageFont.Layout.BASIC)
        try:
            self.axes = probe.get_variation_axes()
        except Exception:
            self.axes = []
        self.is_variable = bool(self.axes)
        self._notdef = bytes(probe.getmask("￿"))
        self._probe = probe

    def has(self, ch: str) -> bool:
        return ch.isspace() or bytes(self._probe.getmask(ch)) != self._notdef

    def get(self, size: int, weight: int) -> ImageFont.FreeTypeFont:
        size = max(6, int(size))
        weight = int(weight)
        key = (size, weight)
        font = self._fonts.get(key)
        if font is None:
            font = ImageFont.truetype(self.path, size, layout_engine=ImageFont.Layout.BASIC)
            if self.is_variable:
                values = []
                for axis in self.axes:
                    name = axis["name"]
                    name = (name.decode() if isinstance(name, bytes) else str(name)).lower()
                    lo, hi = axis["minimum"], axis["maximum"]
                    if name.startswith("weight"):
                        v = weight
                    elif name.startswith("optical"):
                        v = size * 0.75
                    else:
                        v = axis["default"]
                    values.append(max(lo, min(hi, v)))
                font.set_variation_by_axes(values)
            self._fonts[key] = font
        return font


class FontStack:
    """سلسلة خطوط: يُختار لكل حرف أول خط يحتويه."""

    def __init__(self, paths):
        self.fonts = [VariableFont(p) for p in paths if os.path.exists(p)]
        if not self.fonts:
            raise FileNotFoundError("لم يتم العثور على خط عربي مناسب")
        self.primary = self.fonts[0]
        self._pick = {}

    def pick(self, ch: str):
        """يعيد (الخط، الحرف) — قد يُستبدل شكل الحرف المعزول بأصله."""
        hit = self._pick.get(ch)
        if hit is not None:
            return hit
        hit = None
        for vf in self.fonts:
            if all(vf.has(c) for c in ch):
                hit = (vf, ch)
                break
        if hit is None:
            # شكل معزول غير موجود → الحرف الأصلي يُرسم معزولاً افتراضياً
            dec = unicodedata.decomposition(ch) if len(ch) == 1 else ""
            if dec.startswith("<isolated>"):
                base = chr(int(dec.split()[1], 16))
                for vf in self.fonts:
                    if vf.has(base):
                        hit = (vf, base)
                        break
        if hit is None:
            hit = (self.primary, ch)
        self._pick[ch] = hit
        return hit

    def get(self, size, weight):
        return self.primary.get(size, weight)

    def metrics(self, size, weight):
        return self.primary.get(size, weight).getmetrics()

    def advance(self, ch: str, size: int, weight: int) -> float:
        vf, c = self.pick(ch)
        return vf.get(size, weight).getlength(c)

    def runs(self, visual: str):
        """تقسيم النص المرئي إلى مقاطع متتالية لكل منها خط واحد."""
        out = []
        for ch in visual:
            vf, c = self.pick(ch)
            if ch.isspace() and out:
                vf = out[-1][0]
            if out and out[-1][0] is vf:
                out[-1][1] += c
            else:
                out.append([vf, c])
        return out


def _pil_to_surface(img: Image.Image) -> pygame.Surface:
    return pygame.image.frombuffer(img.tobytes(), img.size, "RGBA").convert_alpha()


ROTATION_STEP = 4  # درجات — تكميم زوايا الدوران لتخزينها مسبقاً


class GlyphCache:
    """
    يخزّن أسطح الحروف لكل (حرف، مستوى وزن، مستوى تكبير، لون، زاوية)
    حتى يبقى الرسم سريعاً (60 إطاراً بالثانية).
    مركز السطح = مركز صندوق الحرف، لذلك يبقى خط القاعدة (Baseline) متّسقاً.
    """

    def __init__(self, fonts: FontStack, base_size: int, colors):
        self.fonts = fonts
        self.base_size = base_size
        self.colors = colors
        self._cache = {}
        self._rot = {}

    def get(self, ch: str, weight_idx: int, scale_idx: int, color_idx: int = 0, angle: float = 0.0):
        """يعيد (السطح، ox، oy): إزاحة مركز السطح المقصوص عن مركز صندوق الحرف."""
        q = int(round(angle / ROTATION_STEP))
        if q:
            key = (ch, weight_idx, scale_idx, color_idx, q)
            hit = self._rot.get(key)
            if hit is None:
                base, ox, oy = self.get(ch, weight_idx, scale_idx, color_idx)
                a = math.radians(q * ROTATION_STEP)
                surf = pygame.transform.rotozoom(base, q * ROTATION_STEP, 1.0)
                # تدوير الإزاحة أيضاً حتى يبقى محور الدوران مركز الحرف
                hit = (surf, ox * math.cos(a) + oy * math.sin(a), -ox * math.sin(a) + oy * math.cos(a))
                if len(self._rot) > 6000:
                    self._rot.clear()
                self._rot[key] = hit
            return hit
        key = (ch, weight_idx, scale_idx, color_idx)
        hit = self._cache.get(key)
        if hit is None:
            hit = self._nearest(ch, weight_idx, scale_idx, color_idx)
            if hit is not None:
                # لا نوقف الإطار: نعرض أقرب نسخة جاهزة ونرسم المطلوبة في الخلفية
                if getattr(self, "_queue", None) is not None and key not in self._wanted:
                    self._wanted.add(key)
                    self._queue.appendleft(key)
                return hit
            hit = self._render(ch, weight_idx, scale_idx, color_idx)
            self._cache[key] = hit
        return hit

    def _nearest(self, ch, wi, si, ci):
        """أقرب نسخة مرسومة مسبقاً من الحرف نفسه (وزن/حجم/لون)."""
        best, best_d = None, 1e9
        for c in (ci, 0) if ci else (0,):
            for dw in range(len(config.WEIGHT_LEVELS)):
                for sgn_w in ((0,) if dw == 0 else (-1, 1)):
                    w = wi + sgn_w * dw
                    if not 0 <= w < len(config.WEIGHT_LEVELS):
                        continue
                    for ds in range(len(config.SCALE_LEVELS)):
                        d = dw + ds * 1.5 + (2 if c != ci else 0)
                        if d >= best_d:
                            break
                        for sgn_s in ((0,) if ds == 0 else (-1, 1)):
                            sc = si + sgn_s * ds
                            if 0 <= sc < len(config.SCALE_LEVELS):
                                hit = self._cache.get((ch, w, sc, c))
                                if hit is not None and d < best_d:
                                    best, best_d = hit, d
                if best is not None and best_d <= 1:
                    return best
        return best

    def _render(self, ch, weight_idx, scale_idx, color_idx):
        size = int(round(self.base_size * config.SCALE_LEVELS[scale_idx]))
        weight = config.WEIGHT_LEVELS[weight_idx]
        ascent, descent = self.fonts.metrics(size, weight)
        vf, c = self.fonts.pick(ch)
        font = vf.get(size, weight)
        adv = max(1.0, font.getlength(c))
        pad = int(size * 0.45)
        w = int(math.ceil(adv)) + 2 * pad
        h = ascent + descent + 2 * pad
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        fill = tuple(self.colors[color_idx]) + (255,)
        # محاذاة خط القاعدة مع الخط الأساسي حتى لو جاء الحرف من خط احتياطي
        y = pad + ascent - font.getmetrics()[0]
        # خط غير متغيّر (مثل المسماري): الوزن يُحاكى بسماكة الحدّ تدريجياً
        stroke = 0 if vf.is_variable else int(round(max(0, weight - 400) / 500 * size / 42))
        if stroke:
            draw.text((pad, y), c, font=font, fill=fill, stroke_width=stroke, stroke_fill=fill)
        else:
            draw.text((pad, y), c, font=font, fill=fill)
        # قصّ الفراغ الشفاف: الرسم أسرع بكثير (المساحة المدموجة أصغر)
        bbox = img.getbbox()
        if bbox is None:
            return _pil_to_surface(Image.new("RGBA", (1, 1), (0, 0, 0, 0))), 0.0, 0.0
        x0, y0, x1, y1 = bbox
        ox = (x0 + x1) / 2 - w / 2
        oy = (y0 + y1) / 2 - h / 2
        # A pale lower edge and dark recess make the lettering look incised.
        cropped = img.crop(bbox)
        carved = Image.new("RGBA", (cropped.width+2, cropped.height+2))
        edge = Image.new("RGBA", cropped.size, (230, 185, 135, 0))
        edge.putalpha(cropped.getchannel("A"))
        carved.alpha_composite(edge, (1, 2))
        carved.alpha_composite(cropped, (0, 0))
        return _pil_to_surface(carved), ox+1, oy+1

    def warm(self, chars):
        """
        يُرسم الوزن الأساسي فوراً، وتُوضع بقية الأوزان والأحجام في طابور
        يُرسم تدريجياً (pump) حتى لا يتجمّد العرض عند تغيير النص.
        """
        import collections
        rest = min(range(len(config.WEIGHT_LEVELS)),
                   key=lambda i: abs(config.WEIGHT_LEVELS[i] - config.REST_WEIGHT))
        chars = sorted(chars)
        for ch in chars:
            self.get(ch, rest, 0, 0)
        order = sorted(range(len(config.WEIGHT_LEVELS)), key=lambda i: abs(i - rest))
        self._queue = collections.deque()
        self._wanted = set()
        for wi in order:                                   # الأوزان القريبة أولاً
            for ch in chars:
                self._queue.append((ch, wi, 0, 0))
        for si in range(1, len(config.SCALE_LEVELS)):      # ثم أحجام العدسة بالأوزان الثقيلة
            for wi in order[: len(order) // 2 + 1]:
                for ch in chars:
                    self._queue.append((ch, wi, si, 0))
                    self._queue.append((ch, wi, si, 1))

    def pump(self, budget_s=0.003):
        """يرسم من الطابور ضمن ميزانية زمنية صغيرة في كل إطار."""
        q = getattr(self, "_queue", None)
        if not q:
            return
        import time as _t
        end = _t.perf_counter() + budget_s
        while q and _t.perf_counter() < end:
            key = q.popleft()
            if key not in self._cache:
                self._cache[key] = self._render(*key)


class TextRenderer:
    """رسم نصوص الواجهة العربية (العناوين، المساعدة، الحالة)."""

    def __init__(self, fonts: FontStack):
        self.fonts = fonts
        self._cache = {}

    def render(self, text, size, weight=400, color=(230, 230, 230)):
        key = (text, size, weight, tuple(color))
        surf = self._cache.get(key)
        if surf is not None:
            return surf
        ascent, descent = self.fonts.metrics(size, weight)
        runs = [(vf.get(size, weight), s) for vf, s in self.fonts.runs(shape(text))]
        w = int(math.ceil(sum(f.getlength(s) for f, s in runs))) + 8
        h = ascent + descent + 8
        img = Image.new("RGBA", (max(1, w), h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        x = 4.0
        for f, s in runs:
            draw.text((x, 4 + ascent - f.getmetrics()[0]), s, font=f, fill=tuple(color) + (255,))
            x += f.getlength(s)
        surf = _pil_to_surface(img)
        if len(self._cache) > 400:
            self._cache.clear()
        self._cache[key] = surf
        return surf
