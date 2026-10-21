"""
لقطات حقيقية من التطبيق لفيديو الشرح (بلا نافذة ولا كاميرا): نافذة البدء بحالاتها، ومقاطع من العرض
تُحرَّك فيها «اليد» بالفأرة الوهمية (إمساك حرف وسحبه ورميه، والتقليب بين الشرائح).
الناتج: <out>/launcher_*.png ومجلدات مقاطع <out>/clip_<name>/00000.jpg ... مع hand.json لموضع اليد.

    python tools/video/capture_app.py <out>
"""
import json
import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402
from PIL import Image  # noqa: E402

FPS = 25


def save_surface(surf, path, quality=90):
    img = Image.frombytes("RGB", surf.get_size(), pygame.image.tobytes(surf, "RGB"))
    img.save(path, quality=quality)


def launcher_stills(out):
    from wall import launcher as L
    pygame.init()
    screen = pygame.display.set_mode((L.W, L.H))
    ui = L.Launcher({"source": "esp"}, screen, detect=False)
    states = {
        "launcher_esp_off": dict(source="esp", cam_ok=False, _streak=0, status=("", "dim", "")),
        "launcher_esp_settling": dict(source="esp", cam_ok=False, _streak=1, _joining=True,
                                      status=("أوصل الحاسوب بشبكة الكاميرا… (قد يستغرق بضع ثوانٍ)", "dim", "")),
        "launcher_esp_ok": dict(source="esp", cam_ok=True, _streak=3, _joining=False,
                                status=("تم الاتصال بالكاميرا واستقر. اضغط «ابدأ».", "ok", ""), fullscreen=True),
        "launcher_usb_ok": dict(source="usb", usb_ports=[("COM21", "CH340")], usb_port="COM21",
                                status=("", "dim", ""), fullscreen=True),
    }
    for name, attrs in states.items():
        for k, v in attrs.items():
            setattr(ui, k, v)
        ui.draw()
        save_surface(screen, os.path.join(out, name + ".png"))
    pygame.quit()


def clip(out, name, seconds, script, slide=0):
    """script(t, app) يحرّك الفأرة/الأزرار؛ يعيد (x, y, قبضة) للرسم فوق اللقطة."""
    from wall import settings
    settings.save = lambda data: True
    from wall.app import App
    app = App(use_camera=False)
    app.audio.set_sound(False)
    app.show_help = False
    app.help_until = 0
    app.slide = slide
    app._load_item(entrance=False)
    d = os.path.join(out, "clip_" + name)
    os.makedirs(d, exist_ok=True)
    hands = []
    for i in range(int(seconds * FPS)):
        t = i / FPS
        x, y, grip = script(t, app)
        app.mouse.mouse = (x, y)
        app.mouse.left = grip
        app.update(1.0 / FPS, app.current_input())
        app.draw()
        if app._pending:                       # الانتقال يبدأ في الإطار التالي كما في run()
            app._begin_transition()
        save_surface(app.screen, os.path.join(d, f"{i:05d}.jpg"))
        hands.append([x, y, grip])
    json.dump(hands, open(os.path.join(d, "hand.json"), "w"))
    pygame.quit()
    print(f"  clip_{name}: {len(hands)} frames", flush=True)


def play_script(t, app):
    """تدخل اليد، تتجول فوق الحروف، تمسك حرفاً عند 4 ث، تسحبه، وتفلته (رمي) عند 7.2 ث."""
    app.mouse.proximity = 0.45
    if t < 1.0:
        return 0.85, 0.85 - 0.25 * t, False
    if t < 4.0:
        a = (t - 1.0) / 3.0
        return 0.85 - 0.45 * a, 0.6 - 0.12 * math.sin(a * math.pi), False
    if t < 7.2:
        a = (t - 4.0) / 3.2
        return 0.40 - 0.18 * a, 0.48 + 0.25 * math.sin(a * math.pi * 0.9), True
    a = (t - 7.2) / 2.8
    return 0.22 + 0.5 * a, 0.70 - 0.3 * a, False


def slides_script(t, app):
    """يد تتحرك، ثم انتقال للشريحة المسمارية (2 ث)، ثم الصورة (6.5 ث) مع لعب بالقطع."""
    app.mouse.proximity = 0.45
    if abs(t - 1.6) < 0.5 / FPS:
        app.go_slide(app.slide + 1, "open")
    if abs(t - 6.0) < 0.5 / FPS:
        app.go_slide(app.slide + 1, "open")
    x = 0.5 + 0.25 * math.sin(t * 1.3)
    y = 0.5 + 0.15 * math.sin(t * 0.9 + 1)
    grip = 8.5 < t < 10.5
    return x, y, grip


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    launcher_stills(out)
    print("  launcher stills done", flush=True)
    clip(out, "play", 10.0, play_script, slide=0)
    clip(out, "slides", 12.0, slides_script, slide=0)
