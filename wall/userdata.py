"""مجلد بيانات المستخدم للتطبيق (التفضيلات وسجل الأخطاء)، خارج مجلد البرنامج."""
import os
import sys

APP_DIR_NAME = "TypographyWall"


def data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, APP_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        import tempfile
        path = tempfile.gettempdir()
    return path
