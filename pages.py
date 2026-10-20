"""
صفحات الجدار — ثلاث صفحات لكل كلمة من الكلمات الخمس (15 شريحة):
  1. الخط العربي            الكلمة بخط متغيّر الوزن
  2. الخط المسماري          الكلمة نفسها بالعلامات المسمارية مع قراءتها
  3. صور من بلاد الرافدين    صورة حقيقية تتفكك إلى قطع تُمسك وتُسحب

التنقّل: باعِد بين الكفّين بسرعة = الشريحة التالية، قرّبهما = السابقة (أو ← →).
"""

import json
import os

from . import config
from .texts import WORDS

ROOT = os.path.dirname(os.path.dirname(__file__))
IMAGES_DIR = os.path.join(ROOT, "assets", "mesopotamia")

CUNEIFORM_FONTS = [
    os.environ.get("WALL_CUNEIFORM_FONT", ""),
    os.path.join(ROOT, "assets", "fonts", "NotoSansCuneiform-Regular.ttf"),
    "/usr/share/fonts/truetype/noto/NotoSansCuneiform-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCuneiform-Regular.otf",

    "/System/Library/Fonts/Supplemental/NotoSansCuneiform-Regular.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/SFArabic.ttf",
]


def _credits():
    try:
        with open(os.path.join(IMAGES_DIR, "credits.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _credit_line(c):
    if not c:
        return ""
    author = c.get("author", "").replace("en:User:", "").strip()
    return f"Photo: {author} · {c.get('license', '')} · Wikimedia Commons"


def _image_items():
    credits = _credits()
    items = []
    for w in WORDS:
        path = os.path.join(IMAGES_DIR, w["image"])
        if os.path.exists(path):
            items.append({"path": path, "author": w["image_caption"], "credit": _credit_line(credits.get(w["image"]))})
        else:   # الصورة غير موجودة → لوحة مرسومة برمجياً بدلاً منها
            items.append({"art": w["art"], "author": w["image_caption"] + " · رسم تخيّلي"})
    return items


PAGES = [
    {
        "id": "arabic",
        "name": "الخط العربي",
        "kind": "text",
        "rtl": True,
        "fonts": config.FONT_CANDIDATES,
        "items": [{"text": w["ar_sentence"], "author": w["ar_caption"], "keyword": w["ar"]} for w in WORDS],
        "invite": "اقترب… فالكلمة تنتظر حركتك",
        "grab_hint": "اقرص الحرف المضاء بإصبعيك لتمسكه، وافتحهما لترميه",
        "palette": {
            "bg": (12, 11, 10), "ink": (238, 232, 220), "accent": (226, 120, 72),
            "shadow": (34, 31, 29), "dim": (150, 142, 132),
        },
    },
    {
        "id": "cuneiform",
        "name": "الخط المسماري",
        "kind": "text",
        "rtl": False,
        "fonts": CUNEIFORM_FONTS + config.FONT_CANDIDATES,
        "items": [{"text": w["cu"], "author": w["cu_caption"], "translit": w["cu_translit"]} for w in WORDS],
        "invite": "اقترب… فالعلامات المسمارية تنتظر حركتك",
        "grab_hint": "اقرص العلامة المضاءة بإصبعيك لتمسكها، وافتحهما لترميها",
        "tablet": True,       # لوح طيني خلف العلامات
        "palette": {
            "bg": (22, 16, 12), "ink": (226, 188, 136), "accent": (92, 146, 236),
            "shadow": (46, 34, 26), "dim": (168, 136, 104),
            "panel": (56, 40, 30), "panel_edge": (88, 64, 46),
        },
    },
    {
        "id": "images",
        "name": "صور من بلاد الرافدين",
        "kind": "image",
        "items": _image_items(),
        "invite": "اقترب… فالصورة تنتظر حركتك",
        "grab_hint": "اقرص القطعة المضاءة بإصبعيك لتمسكها، وافتحهما لترميها",
        "palette": {
            "bg": (9, 13, 26), "ink": (232, 222, 200), "accent": (238, 186, 82),
            "shadow": (26, 34, 60), "dim": (140, 150, 178),
        },
    },
]

# Unified clay palette for text and image exhibits.
for page in PAGES:
    page["tablet"] = False  # the entire background is now a clay tablet
    page["palette"] = {
        "bg": (165, 119, 79), "ink": (63, 36, 23), "accent": (106, 47, 25),
        "shadow": (149, 101, 65), "dim": (92, 59, 39),
        "panel": (181, 134, 92), "panel_edge": (127, 82, 50),
    }
