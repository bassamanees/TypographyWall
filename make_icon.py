"""
أيقونة التطبيق وشاشة البداية: لوح طيني بني وفيه علامة مسمارية واحدة محفورة بدرجة بنية أغمق.
العلامة 𒀭 (دِنْجِر: النجمة، «الإله») — وهي العلامة الثانية في كلمة بابل داخل التطبيق.

    python tools/make_icon.py        → app_icon.png، app.ico، splash.png
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "assets", "fonts", "NotoSansCuneiform-Regular.ttf")
SIGN = "\U0001202D"                     # 𒀭 DINGIR

CLAY = (165, 119, 79)        # لون الطين (نفس لوحة ألوان التطبيق)
CLAY_LIGHT = (184, 138, 96)
CLAY_DARK = (128, 86, 54)
INK = (70, 40, 24)           # بني غامق للعلامة المحفورة
EDGE = (110, 72, 44)


def clay_tile(size):
    """مربع بزوايا مستديرة بتدرّج خفيف وحافة، كلوح طين."""
    S = size
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGB", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(CLAY_LIGHT[i] + (CLAY_DARK[i] - CLAY_LIGHT[i]) * t * 0.8) for i in range(3))
        gd.line([(0, y), (S, y)], fill=c)
    mask = Image.new("L", (S, S), 0)
    r = int(S * 0.2)
    m = int(S * 0.03)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], r, fill=255)
    tile.paste(grad, (0, 0), mask)
    ImageDraw.Draw(tile).rounded_rectangle([m, m, S - m, S - m], r, outline=EDGE + (255,),
                                           width=max(2, S // 64))
    return tile


def sign_layer(size, scale=0.62):
    """العلامة المسمارية بالحجم المطلوب ومتمركزة."""
    S = size
    font = ImageFont.truetype(FONT, int(S * scale))
    layer = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = d.textbbox((0, 0), SIGN, font=font)
    d.text(((S - (x1 - x0)) / 2 - x0, (S - (y1 - y0)) / 2 - y0), SIGN, font=font, fill=255)
    return layer


def icon(size=1024):
    S = size
    img = clay_tile(S)
    sign = sign_layer(S)
    off = max(1, S // 90)
    # حفر في الطين: ظل غامق في الأعلى وإضاءة فاتحة في الأسفل حول العلامة
    hi = Image.new("RGBA", (S, S), CLAY_LIGHT + (0,))
    hi.putalpha(sign.filter(ImageFilter.GaussianBlur(S / 300)).point(lambda v: int(v * 0.8)))
    img.alpha_composite(hi, (0, off))
    ink = Image.new("RGBA", (S, S), INK + (255,))
    ink.putalpha(sign)
    img.alpha_composite(ink)
    return img


def save_icons():
    big = icon(1024)
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(ROOT, "app_icon.png"))
    # الأحجام الصغيرة تُرسم من الكبيرة بتنعيم عالٍ
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.save(os.path.join(ROOT, "app.ico"), sizes=sizes)
    print("app_icon.png, app.ico")
    return big


def save_splash(big):
    W, H = 480, 300
    img = Image.new("RGB", (W, H), CLAY)
    d = ImageDraw.Draw(img)
    for y in range(H):
        c = int(14 * y / H)
        d.line([(0, y), (W, y)], fill=(CLAY[0] - c, CLAY[1] - c, CLAY[2] - c))
    ic = big.resize((120, 120), Image.LANCZOS)
    img.paste(ic, ((W - 120) // 2, 40), ic)
    f = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 30)
    s = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 15)
    for text, font, y, col in (("Typography Wall", f, 176, INK),
                               ("Mesopotamian interactive typography", s, 216, (92, 59, 39))):
        d.text(((W - d.textlength(text, font=font)) / 2, y), text, font=font, fill=col)
    d.rectangle([0, 276, W, H], fill=(63, 36, 23))
    img.save(os.path.join(ROOT, "splash.png"))
    print("splash.png")


if __name__ == "__main__":
    save_splash(save_icons())
