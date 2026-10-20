"""
لوحات من بلاد الرافدين مرسومة برمجياً (رسوم تخيّلية مستوحاة من الآثار):
  ishtar    بوابة عشتار — الطابوق الأزرق المزجّج، الأسود، الوريدات
  ziggurat  زقورة أور عند الغروب
  symbols   رموز سين وعشتار وشمش على حجر كودورو

تُرسم بضعف الدقة ثم تُصغَّر لحواف ناعمة. تُستخدم بديلاً إن غابت الصور الحقيقية.
"""

import math
import os
import threading

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 800
S = 2  # عامل الرسم المضاعف

_cache = {}
_lock = threading.Lock()

CUNEIFORM_FONT = "/System/Library/Fonts/Supplemental/NotoSansCuneiform-Regular.ttf"


def _s(v):
    return int(round(v * S))


def _box(x0, y0, x1, y1):
    return [_s(x0), _s(y0), _s(x1), _s(y1)]


def _gradient(w, h, top, bottom):
    t = np.linspace(0, 1, h)[:, None, None]
    col = np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t
    return np.repeat(col, w, axis=1).astype(np.uint8)


def _finish(img):
    return img.resize((W, H), Image.LANCZOS)


# ----------------------------------------------------------------------
# بوابة عشتار
# ----------------------------------------------------------------------
def _bricks(draw, rng, x0, y0, x1, y1, base, bw=48, bh=22, mortar=(14, 34, 80)):
    draw.rectangle(_box(x0, y0, x1, y1), fill=mortar)
    row = 0
    y = y0
    while y < y1:
        off = (bw / 2) if row % 2 else 0
        x = x0 - off
        while x < x1:
            v = rng.integers(-16, 17)
            c = tuple(int(np.clip(b + v + (6 if (row + int(x)) % 7 == 0 else 0), 0, 255)) for b in base)
            bx0, bx1 = max(x, x0) + 1, min(x + bw, x1) - 1
            if bx1 > bx0:
                draw.rectangle(_box(bx0, y + 1, bx1, min(y + bh, y1) - 1), fill=c)
                # لمعة الطلاء الزجاجي
                hl = tuple(min(255, ch + 22) for ch in c)
                draw.line([_s(bx0 + 2), _s(y + 3), _s(bx1 - 3), _s(y + 3)], fill=hl, width=S)
            x += bw
        y += bh
        row += 1


def _rosette(draw, cx, cy, r, petals=8, col=(246, 238, 214), center=(226, 176, 70)):
    for k in range(petals):
        a = 2 * math.pi * k / petals
        px, py = cx + math.cos(a) * r * 0.55, cy + math.sin(a) * r * 0.55
        draw.ellipse(_box(px - r * 0.42, py - r * 0.42, px + r * 0.42, py + r * 0.42), fill=col)
    draw.ellipse(_box(cx - r * 0.38, cy - r * 0.38, cx + r * 0.38, cy + r * 0.38), fill=center)


def _lion(img, x, y, scale, facing_right=True):
    """أسد سائر مبسّط بأسلوب نقوش الطابوق المزجّج (170×100 قبل التحجيم)."""
    w, h = 170, 100
    layer = Image.new("RGBA", (_s(w), _s(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    body, mane, line = (228, 172, 74, 255), (178, 112, 42, 255), (248, 238, 212, 255)
    lw = 2 * S
    # الذيل
    d.arc(_box(0, 8, 46, 70), 200, 300, fill=line, width=3 * S)
    d.ellipse(_box(2, 10, 16, 24), fill=mane, outline=line, width=lw)
    # الأرجل
    for lx, ly in ((40, 98), (60, 95), (106, 98), (124, 95)):
        d.rounded_rectangle(_box(lx, 58, lx + 13, ly), radius=_s(4), fill=body, outline=line, width=lw)
    # الجسم
    d.rounded_rectangle(_box(26, 28, 132, 72), radius=_s(20), fill=body, outline=line, width=lw)
    # اللبدة والرأس
    d.ellipse(_box(96, 6, 152, 74), fill=mane, outline=line, width=lw)
    d.ellipse(_box(120, 16, 164, 54), fill=body, outline=line, width=lw)
    d.ellipse(_box(124, 10, 136, 22), fill=body, outline=line, width=lw)          # الأذن
    d.line([_s(146), _s(44), _s(162), _s(44)], fill=line, width=lw)              # الفم
    d.ellipse(_box(146, 26, 152, 32), fill=(40, 30, 20, 255))                    # العين
    # خطوط عضلات بسيطة
    d.arc(_box(40, 34, 80, 70), 200, 320, fill=line, width=S)
    if not facing_right:
        layer = layer.transpose(Image.FLIP_LEFT_RIGHT)
    if scale != 1.0:
        layer = layer.resize((int(layer.width * scale), int(layer.height * scale)), Image.LANCZOS)
    img.alpha_composite(layer, (_s(x), _s(y)))


def ishtar_gate():
    rng = np.random.default_rng(7)
    img = Image.fromarray(_gradient(_s(W), _s(H), (236, 214, 176), (206, 178, 132))).convert("RGBA")
    d = ImageDraw.Draw(img)
    lapis, lapis_dark = (34, 78, 164), (26, 62, 138)

    # البرجان والجدار
    _bricks(d, rng, 0, 150, W, H, lapis_dark)
    _bricks(d, rng, 170, 70, 430, H, lapis)
    _bricks(d, rng, 770, 70, 1030, H, lapis)
    # الشرفات المدرّجة
    def merlons(x0, x1, y, col):
        x = x0
        while x + 60 <= x1 + 1:
            for k, (ww, hh) in enumerate(((60, 20), (40, 20), (20, 20))):
                cx = x + 30
                d.rectangle(_box(cx - ww / 2, y - (k + 1) * hh, cx + ww / 2, y - k * hh), fill=col)
            x += 72
    merlons(0, 170, 150, lapis_dark)
    merlons(1030, W, 150, lapis_dark)
    merlons(176, 430, 70, lapis)
    merlons(776, 1030, 70, lapis)

    # أشرطة الوريدات
    for y in (110, 330, 560):
        d.rectangle(_box(170, y - 18, 430, y + 18), fill=(20, 44, 104))
        d.rectangle(_box(770, y - 18, 1030, y + 18), fill=(20, 44, 104))
        for x in range(192, 420, 37):
            _rosette(d, x, y, 15)
        for x in range(792, 1020, 37):
            _rosette(d, x, y, 15)

    # المدخل المقوّس
    frame = (238, 206, 120)
    d.rectangle(_box(452, 330, 748, H), fill=frame)
    d.ellipse(_box(452, 182, 748, 478), fill=frame)
    d.rectangle(_box(472, 330, 728, H), fill=(28, 22, 18))
    d.ellipse(_box(472, 202, 728, 458), fill=(28, 22, 18))
    for k in range(10):   # عمق المدخل
        c = 28 + k * 3
        d.rectangle(_box(492 + k * 4, 340 + k * 6, 708 - k * 4, H), fill=(c, c - 6, c - 10))
    _bricks(d, rng, 430, 150, 452, H, lapis_dark)
    _bricks(d, rng, 748, 150, 770, H, lapis_dark)
    # وريدات حول القوس
    for k in range(13):
        a = math.pi + math.pi * k / 12
        _rosette(d, 600 + math.cos(a) * 160, 330 + math.sin(a) * 160, 11)

    # صفوف الأسود متجهة نحو المدخل
    for y in (170, 400, 620):
        _lion(img, 205, y, 1.0, facing_right=True)
        _lion(img, 825, y, 1.0, facing_right=False)

    # أرضية
    d = ImageDraw.Draw(img)
    d.rectangle(_box(0, 770, W, H), fill=(150, 120, 86))
    return _finish(img.convert("RGB"))


# ----------------------------------------------------------------------
# زقورة أور
# ----------------------------------------------------------------------
def _palm(d, x, ground, height, lean=0.0, col=(36, 26, 26)):
    pts = []
    for k in range(12):
        t = k / 11
        pts.append((_s(x + lean * height * t * t), _s(ground - height * t)))
    d.line(pts, fill=col, width=_s(9))
    tx, ty = x + lean * height, ground - height
    for k in range(9):
        a = math.pi * (0.95 + 1.1 * k / 8)
        fr = []
        for j in range(10):
            t = j / 9
            r = 80 * t
            fx = tx + math.cos(a) * r
            fy = ty + math.sin(a) * r + 38 * t * t   # تدلّي السعف
            fr.append((_s(fx), _s(fy)))
        d.line(fr, fill=col, width=_s(6))


def _terrace(d, bottom, top, yb, yt, face, edge, lines):
    (bl, br), (tl, tr) = bottom, top
    d.polygon([(_s(bl), _s(yb)), (_s(br), _s(yb)), (_s(tr), _s(yt)), (_s(tl), _s(yt))], fill=face)
    for y in np.arange(yt + 9, yb, 9):          # مداميك اللَّبِن
        t = (y - yt) / (yb - yt)
        xl, xr = tl + (bl - tl) * t, tr + (br - tr) * t
        d.line([_s(xl), _s(y), _s(xr), _s(y)], fill=lines, width=S)
    for k in range(1, 12):                       # الدعامات
        t0 = k / 12
        xa, xb = tl + (tr - tl) * t0, bl + (br - bl) * t0
        d.line([_s(xa), _s(yt), _s(xb), _s(yb)], fill=lines, width=S)
    d.line([_s(tl), _s(yt), _s(tr), _s(yt)], fill=edge, width=_s(3))


def ziggurat():
    sky = _gradient(_s(W), _s(560), (22, 30, 76), (240, 150, 82))
    ground = _gradient(_s(W), _s(H - 560), (190, 148, 98), (128, 94, 60))
    img = Image.fromarray(np.vstack([sky, ground]))
    # الشمس وتوهّجها
    glow = Image.new("L", img.size, 0)
    ImageDraw.Draw(glow).ellipse(_box(780, 380, 1020, 620), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(_s(40)))
    img = Image.composite(Image.new("RGB", img.size, (255, 214, 150)), img, glow)
    d = ImageDraw.Draw(img)
    d.ellipse(_box(860, 460, 950, 550), fill=(255, 226, 170))
    # ضباب الأفق
    d.rectangle(_box(0, 552, W, 568), fill=(214, 160, 110))

    face1, face2, face3 = (176, 124, 80), (166, 116, 74), (156, 108, 70)
    lines = (138, 94, 60)
    _terrace(d, (210, 990), (265, 935), 660, 520, face1, (204, 158, 112), lines)
    _terrace(d, (355, 845), (390, 810), 520, 430, face2, (196, 150, 104), lines)
    _terrace(d, (468, 732), (492, 708), 430, 372, face3, (190, 144, 100), lines)
    # المعبد في القمة
    d.rectangle(_box(545, 318, 655, 372), fill=(184, 134, 90))
    d.rectangle(_box(588, 336, 612, 372), fill=(70, 44, 30))
    d.line([_s(540), _s(318), _s(660), _s(318)], fill=(214, 168, 120), width=_s(4))
    # الدرج المركزي والجانبيان
    stair = (204, 156, 108)
    d.polygon([(_s(560), _s(660)), (_s(640), _s(660)), (_s(622), _s(430)), (_s(578), _s(430))], fill=stair)
    for y in range(436, 660, 7):
        t = (y - 430) / 230
        d.line([_s(578 - 18 * t), _s(y), _s(622 + 18 * t), _s(y)], fill=lines, width=S)
    for sgn in (-1, 1):
        x_out, x_in = 600 + sgn * 330, 600 + sgn * 70
        d.polygon([(_s(x_out), _s(660)), (_s(x_out - sgn * 46), _s(660)),
                   (_s(x_in - sgn * 2), _s(530)), (_s(x_in + sgn * 30), _s(530))], fill=stair)
        for k in range(1, 18):
            t = k / 18
            xa = x_out + (x_in + sgn * 30 - x_out) * t
            xb = x_out - sgn * 46 + (x_in - sgn * 2 - (x_out - sgn * 46)) * t
            y = 660 - 130 * t
            d.line([_s(xa), _s(y), _s(xb), _s(y)], fill=lines, width=S)
    # ظلّ على الأرض
    d.polygon([(_s(210), _s(660)), (_s(990), _s(660)), (_s(1060), _s(700)), (_s(160), _s(700))],
              fill=(120, 88, 58))
    _palm(d, 90, 720, 230, lean=0.12)
    _palm(d, 1090, 730, 250, lean=-0.10)
    _palm(d, 1020, 705, 170, lean=0.05)
    return _finish(img)


# ----------------------------------------------------------------------
# رموز سين وعشتار وشمش (كودورو)
# ----------------------------------------------------------------------
def _relief(img, mask, stone):
    """نحت بارز: ظلّ سفلي، إضاءة علوية، ثم سطح الحجر."""
    m = mask.filter(ImageFilter.GaussianBlur(S))
    shadow = Image.new("RGB", img.size, tuple(int(c * 0.62) for c in stone))
    light = Image.new("RGB", img.size, tuple(min(255, int(c * 1.22)) for c in stone))
    face = Image.new("RGB", img.size, tuple(min(255, int(c * 1.06)) for c in stone))
    off = _s(4)
    img.paste(shadow, (0, 0), m.transform(m.size, Image.AFFINE, (1, 0, -off, 0, 1, -off)))
    img.paste(light, (0, 0), m.transform(m.size, Image.AFFINE, (1, 0, off // 2, 0, 1, off // 2)))
    img.paste(face, (0, 0), m)


def celestial_symbols():
    rng = np.random.default_rng(3)
    img = Image.new("RGB", (_s(W), _s(H)), (26, 22, 20))
    stone = (150, 138, 116)
    # جسم المسلّة (قمة مستديرة) مع حبيبات الحجر
    stele = Image.new("L", img.size, 0)
    sd = ImageDraw.Draw(stele)
    sd.rectangle(_box(300, 250, 900, 770), fill=255)
    sd.ellipse(_box(300, 40, 900, 460), fill=255)
    grain = (rng.normal(0, 9, (img.size[1], img.size[0], 1)) + np.array(stone)).clip(0, 255).astype(np.uint8)
    img.paste(Image.fromarray(grain), (0, 0), stele)
    d = ImageDraw.Draw(img)
    edge = tuple(int(c * 0.7) for c in stone)
    d.arc(_box(318, 58, 882, 442), 180, 360, fill=edge, width=_s(4))
    d.line([_s(318), _s(250), _s(318), _s(752)], fill=edge, width=_s(4))
    d.line([_s(882), _s(250), _s(882), _s(752)], fill=edge, width=_s(4))

    cy, r = 270, 72
    # سين: هلال
    m = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(m)
    md.ellipse(_box(420 - r, cy - r, 420 + r, cy + r), fill=255)
    md.ellipse(_box(420 - r * 0.78, cy - r * 1.18, 420 + r * 1.05, cy + r * 0.62), fill=0)
    _relief(img, m, stone)
    # عشتار: نجمة ثمانية داخل دائرة
    m = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(m)
    md.ellipse(_box(600 - r, cy - r, 600 + r, cy + r), outline=255, width=_s(7))
    pts = []
    for k in range(16):
        rr = r * (0.86 if k % 2 == 0 else 0.34)
        a = math.pi * k / 8 - math.pi / 2
        pts.append((_s(600 + math.cos(a) * rr), _s(cy + math.sin(a) * rr)))
    md.polygon(pts, fill=255)
    _relief(img, m, stone)
    # شمش: قرص بنجمة رباعية وأشعة متموّجة
    m = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(m)
    md.ellipse(_box(780 - r, cy - r, 780 + r, cy + r), outline=255, width=_s(7))
    pts = []
    for k in range(8):
        rr = r * (0.86 if k % 2 == 0 else 0.16)
        a = math.pi * k / 4 - math.pi / 2
        pts.append((_s(780 + math.cos(a) * rr), _s(cy + math.sin(a) * rr)))
    md.polygon(pts, fill=255)
    for k in range(4):
        base = math.pi / 4 + k * math.pi / 2
        for j in (-1, 0, 1):
            a0 = base + j * 0.2
            wave = []
            for q in range(14):
                t = q / 13
                rr = r * (0.22 + 0.6 * t)
                a = a0 + 0.06 * math.sin(t * 3 * math.pi)
                wave.append((_s(780 + math.cos(a) * rr), _s(cy + math.sin(a) * rr)))
            md.line(wave, fill=255, width=_s(4))
    _relief(img, m, stone)

    # أشرطة السجلّات ونقش مسماري: أسماء الآلهة الثلاثة
    for y in (390, 520, 650):
        d.line([_s(330), _s(y), _s(870), _s(y)], fill=edge, width=_s(3))
    if os.path.exists(CUNEIFORM_FONT):
        font = ImageFont.truetype(CUNEIFORM_FONT, _s(50))
        m = Image.new("L", img.size, 0)
        md = ImageDraw.Draw(m)
        text = "𒀭𒂗𒍪  𒀭𒈹  𒀭𒌓"
        tw = md.textlength(text, font=font)
        md.text(((img.size[0] - tw) / 2, _s(418)), text, font=font, fill=255)
        _relief(img, m, stone)
        font2 = ImageFont.truetype(CUNEIFORM_FONT, _s(40))
        m = Image.new("L", img.size, 0)
        md = ImageDraw.Draw(m)
        text2 = "𒈗 𒆠 𒂍 𒁾 𒀀 𒌓"
        tw = md.textlength(text2, font=font2)
        md.text(((img.size[0] - tw) / 2, _s(560)), text2, font=font2, fill=255)
        _relief(img, m, stone)
    return _finish(img)


ARTWORKS = {"ishtar": ishtar_gate, "ziggurat": ziggurat, "symbols": celestial_symbols}


def get(name):
    """صورة PIL للّوحة (تُرسم مرة واحدة ثم تُحفظ في الذاكرة)."""
    with _lock:
        img = _cache.get(name)
    if img is None:
        img = ARTWORKS[name]()
        with _lock:
            _cache[name] = img
    return img
