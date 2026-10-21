# -*- mode: python ; coding: utf-8 -*-
# بناء التطبيق:  python -m PyInstaller wall_app.spec   (أو شغّل build_windows.bat)
import os
import sys
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(SPECPATH)
SKIP = {"wall", "build", "dist", "venv", ".venv", "env", "__pycache__", ".git", ".idea", ".vscode",
        "tests", "docs", "logs", "tools", "recordings", "screenshots", "release", "github_upload", "firmware"}

datas, binaries, hiddenimports = [], [], []

# MediaPipe: نماذجه ومكتباته الأصلية
d, b, h = collect_all("mediapipe")
datas += d; binaries += b; hiddenimports += h

# كل مجلدات البيانات في جذر المشروع (assets, models, ... ) تُنسخ كما هي
for name in os.listdir(ROOT):
    p = os.path.join(ROOT, name)
    if os.path.isdir(p) and name not in SKIP and not name.startswith("."):
        datas.append((p, name))

# ملفات غير بايثون داخل wall/ (إن وُجدت)
wall_dir = os.path.join(ROOT, "wall")
for dirpath, dirs, files in os.walk(wall_dir):
    dirs[:] = [x for x in dirs if x != "__pycache__"]
    for f in files:
        if not f.endswith((".py", ".pyc")):
            rel = os.path.relpath(dirpath, ROOT)
            datas.append((os.path.join(dirpath, f), rel))

for f in ("app.ico", "app_icon.png"):
    if os.path.isfile(os.path.join(ROOT, f)):
        datas.append((os.path.join(ROOT, f), "."))

hiddenimports += ["wall.app", "wall.launcher", "wall.esp", "wall.userdata", "wall.tracking",
                  "wall.gestures", "wall.interaction", "wall.filters", "wall.landmarkers",
                  "wall.arabic", "wall.config", "wall.diagnostics", "wall.audio", "wall.guide_texts", "wall.usbcam",
                  "serial", "serial.tools.list_ports",
                  "tkinter", "tkinter.messagebox"]

MAC = sys.platform == "darwin"
# WALL_ONEFILE=1 → ملف واحد فيه كل شيء (build_onefile.bat، وعلى لينكس)، وإلا مجلد (build_windows.bat).
# على الماك يُبنى دائماً تطبيق .app (الملف الواحد وشاشة البداية غير مدعومين هناك).
ONEFILE = os.environ.get("WALL_ONEFILE") == "1" and not MAC

a = Analysis(["main.py"], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
             # أدوات توليد الدليل الصوتي فقط. (matplotlib يلزم: MediaPipe يستورده عند البدء)
             excludes=["edge_tts", "aiohttp"], noarchive=False)
pyz = PYZ(a.pure)
if MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TypographyWall",
              debug=False, strip=False, upx=False, console=False, argv_emulation=False)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TypographyWall")
    app = BUNDLE(coll, name="TypographyWall.app", icon="app_icon.png",
                 bundle_identifier="org.typographywall.app",
                 info_plist={
                     # بدون هذا السطر يمنع macOS الكاميرا عن التطبيق
                     "NSCameraUsageDescription": "Typography Wall uses the camera to follow your hands.",
                     "NSHighResolutionCapable": True,
                     "CFBundleShortVersionString": "1.0",
                 })
elif ONEFILE:
    # الملف الواحد يُفك في مجلد مؤقت عند كل تشغيل؛ شاشة البداية تظهر أثناء ذلك
    splash = Splash("splash.png", binaries=a.binaries, datas=a.datas,
                    text_pos=(24, 286), text_size=10, text_color="#f0e4cc", always_on_top=True)
    exe = EXE(pyz, a.scripts, splash, splash.binaries, a.binaries, a.datas, [],
              name="TypographyWall", debug=False, strip=False, upx=False, console=False,
              icon="app.ico" if sys.platform == "win32" else None, runtime_tmpdir=None)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TypographyWall",
              debug=False, strip=False, upx=False, console=False,
              icon="app.ico" if sys.platform == "win32" else None)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TypographyWall")
