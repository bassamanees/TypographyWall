import argparse
import os
import sys
import threading
import traceback

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ required")

# تطبيق بلا نافذة طرفية (windowed): لا stdout ولا stderr، فنحوّلهما إلى الفراغ كي لا ينهار أي print
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

# يجب ضبطها قبل استيراد pygame
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from wall.diagnostics import configure  # noqa: E402

configure()

# شاشة البداية في نسخة الملف الواحد (تظهر أثناء فك الملف قبل أن يبدأ بايثون)
try:
    import pyi_splash  # noqa: E402
    pyi_splash.update_text("Starting…")
except ImportError:
    pyi_splash = None


def close_splash():
    global pyi_splash
    if pyi_splash is not None:
        try:
            pyi_splash.close()
        except Exception:
            pass
        pyi_splash = None


def report_crash():
    """يكتب الخطأ في error.log ويعرضه للمستخدم (التطبيق لا نافذة طرفية له)."""
    text = traceback.format_exc()
    path = None
    try:
        from wall.userdata import data_dir
        path = os.path.join(data_dir(), "error.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    try:
        sys.stderr.write(text)
    except Exception:
        pass
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk()
        r.withdraw()
        messagebox.showerror("Typography Wall", "The app stopped because of an error.\n\n"
                             + text.strip().splitlines()[-1] + ("\n\nDetails: " + path if path else ""))
        r.destroy()
    except Exception:
        pass


def selftest():
    """
    فحص سريع للتطبيق المحزَّم بلا كاميرا ولا شاشة (يُستعمل في بناء GitHub لكل نظام):
    نموذجا MediaPipe، والخطوط، والأصوات، و60 إطاراً من التطبيق بوضع الفأرة. يكتب النتيجة في selftest.txt.
    """
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import numpy as np
    from wall import settings
    from wall.landmarkers import HandsTask, PoseTask
    lines = []
    with HandsTask() as h:
        h.process(np.zeros((240, 320, 3), np.uint8))
    with PoseTask() as p:
        p.process(np.zeros((240, 320, 3), np.uint8))
    lines.append("mediapipe hand+pose models: ok")
    settings.save = lambda data: True          # لا نكتب إعدادات المستخدم أثناء الفحص
    from wall.app import App
    app = App(use_camera=False)
    for _ in range(60):
        app.update(1 / 60, app.current_input())
        app.draw()
    lines.append(f"app frames: ok (slide {app.slide}, audio files: {len(app.audio._sfx)} effects)")
    from wall.audio import AUDIO_DIR
    guide = [f for f in os.listdir(os.path.join(AUDIO_DIR, "guide")) if f.endswith(".mp3")]
    assert len(guide) == 16, f"guide files: {len(guide)}"
    lines.append(f"guide files: {len(guide)}")
    text = "SELFTEST OK\n" + "\n".join(lines) + "\n"
    with open(os.environ.get("WALL_SELFTEST_OUT", "selftest.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    sys.stdout.write(text)


def main():
    if "--selftest" in sys.argv:
        close_splash()
        return selftest()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mouse", action="store_true", help="استخدام الفأرة بدل الكاميرا (بلا نافذة البدء)")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--camera", default=None,
                        help="رقم الكاميرا (0) أو رابط البث مثل http://192.168.1.50:81/stream (بلا نافذة البدء)")
    args = parser.parse_args()

    if args.mouse or args.camera is not None:
        use_camera = not args.mouse
        camera = args.camera if args.camera is not None else "0"
        camera = int(camera) if camera.isdigit() else camera
        fullscreen = args.fullscreen
    else:
        # نافذة البدء: اختيار الكاميرا. أثناءها نحمّل مكتبات التطبيق الثقيلة في الخلفية لتسريع الانطلاق
        loaded = {}

        def preload():
            try:
                from wall.app import App
                loaded["App"] = App
            except BaseException:
                loaded["error"] = traceback.format_exc()

        t = threading.Thread(target=preload, daemon=True)
        t.start()
        from wall.launcher import run_launcher
        close_splash()
        choice = run_launcher()
        if choice is None:
            return
        use_camera, camera = choice["use_camera"], choice["camera"]
        fullscreen = choice["fullscreen"] or args.fullscreen
        t.join()
        if "error" in loaded:
            raise RuntimeError(loaded["error"])

    from wall.app import App
    close_splash()
    App(use_camera=use_camera, fullscreen=fullscreen, camera_index=camera).run()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        close_splash()
        report_crash()
        sys.exit(1)
