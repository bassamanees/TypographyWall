"""
الحالة الساكنة (Resting State): توزيع الحروف على شبكة تصميمية صارمة.

- حجم الخط يُحسب تلقائياً ليملأ مساحة الشبكة
- الأسطر على شبكة خطوط قاعدة ثابتة (Baseline Grid)
- كل سطر مضبوط (Justified) على كامل عرض الكتلة، كما في كتابة شطري البيت
- الكلمات تُرتّب من اليمين لليسار
"""

from dataclasses import dataclass, field

from . import config
from .arabic import FontStack, shape


@dataclass
class Glyph:
    ch: str
    x: float          # مركز الحرف الأصلي (الموضع في الشبكة)
    y: float
    line: int
    adv: float
    emph: bool = False   # جزء من الكلمة المفتاحية (تُرسم بوزن أثقل)


@dataclass
class Layout:
    glyphs: list
    font_size: int
    block: tuple                      # (x, y, w, h)
    baselines: list = field(default_factory=list)
    columns: list = field(default_factory=list)


def _wrap(words, measure, max_w, space_w):
    lines, cur, cur_w = [], [], 0.0
    for wd in words:
        w = measure(wd)
        add = w if not cur else cur_w + space_w + w
        if cur and add > max_w:
            lines.append(cur)
            cur, cur_w = [wd], w
        else:
            cur.append(wd)
            cur_w = add
    if cur:
        lines.append(cur)
    return lines


def build_layout(text: str, fonts: FontStack, screen_w: int, screen_h: int, rtl: bool = True,
                 max_size: float = 0.16, keyword: str = None, word_mode: bool = False) -> Layout:
    """rtl=True للعربية (الكلمات من اليمين لليسار)، و False للمسماري (من اليسار لليمين)."""
    mx = int(screen_w * config.GRID_MARGIN_X)
    my = int(screen_h * config.GRID_MARGIN_Y)
    block_w = screen_w - 2 * mx
    block_h = int((screen_h - 2 * my) * 0.82)   # يُترك أسفل الكتلة مكان لاسم القائل
    paragraphs = [p.split() for p in text.split("\n") if p.strip()]

    if not paragraphs:
        return Layout([], 12, (mx, my, block_w, 0))
    # المحاولة الأولى: كل سطر مكتوب (\n) يبقى سطراً واحداً — بلا كلمات يتيمة.
    # لا نسمح باللفّ التلقائي إلا إذا صار الخط أصغر من الحد الأدنى المقروء.
    min_nowrap = screen_h * 0.055
    for allow_wrap in (False, True):
        size = int(screen_h * max_size)
        while size > 12:
            adv = lambda c, sz=size: fonts.advance(c, sz, config.REST_WEIGHT)
            measure = lambda wd, a=adv: sum(a(c) for c in shape(wd))
            space_w = adv(" ") * 1.2
            if allow_wrap:
                lines = []
                for words in paragraphs:
                    lines.extend(_wrap(words, measure, block_w, space_w))
            else:
                lines = [list(words) for words in paragraphs]
            line_h = size * config.GRID_LINE_HEIGHT
            widest = max(sum(measure(w) for w in ln) + space_w * (len(ln) - 1) for ln in lines)
            if widest <= block_w and line_h * len(lines) <= block_h:
                break
            size -= 2
        if allow_wrap or size >= min_nowrap:
            break

    ascent, descent = fonts.metrics(size, config.REST_WEIGHT)
    adv = lambda c: fonts.advance(c, size, config.REST_WEIGHT)
    line_h = size * config.GRID_LINE_HEIGHT
    total_h = line_h * len(lines)
    top = (screen_h - total_h) / 2 - size * 0.25
    right = mx + block_w

    glyphs, baselines = [], []
    for li, words in enumerate(lines):
        baseline = top + li * line_h + (line_h + ascent - descent) / 2
        baselines.append(baseline)
        cy = baseline - ascent + (ascent + descent) / 2
        shaped = [shape(w) for w in words]
        widths = [sum(adv(c) for c in s) for s in shaped]
        if len(words) > 1:
            gap = (block_w - sum(widths)) / (len(words) - 1)
            gap = min(gap, size * 2.2)   # لا نفرط في التباعد
            line_w = sum(widths) + gap * (len(words) - 1)
        else:
            gap = 0
            line_w = widths[0]
        x = right - (block_w - line_w) / 2 if rtl else mx + (block_w - line_w) / 2
        for word, s, w in zip(words, shaped, widths):
            gx = x - w if rtl else x   # الحروف المرئية تُرسم من اليسار لليمين داخل الكلمة
            emph = bool(keyword) and keyword in word
            units = [s] if word_mode else s
            for c in units:
                a = adv(c)
                if not c.isspace():
                    glyphs.append(Glyph(c, gx + a / 2, cy, li, a, emph))
                gx += a
            x = x - (w + gap) if rtl else x + w + gap

    columns = [mx + block_w * i / 6 for i in range(7)]
    return Layout(glyphs, size, (mx, top, block_w, total_h), baselines, columns)
