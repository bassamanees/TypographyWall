"""Validated settings and atomic writes; failures remain visible in logs."""
import json
import logging
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _default_data_dir():
    # التطبيق المحزَّم: مجلد البرنامج قد لا يقبل الكتابة، فالبيانات في %LOCALAPPDATA%\TypographyWall
    if getattr(sys, 'frozen', False):
        from .userdata import data_dir
        return data_dir()
    return ROOT


DATA_DIR = Path(os.environ.get('WALL_DATA_DIR') or _default_data_dir())
PATH = DATA_DIR / 'settings.json'
log = logging.getLogger(__name__)

def validate(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    slide = data.get('slide')
    if type(slide) is int and slide >= 0:
        result['slide'] = slide
    threshold = data.get('near_threshold')
    if (data.get('calibrated') is True and type(threshold) in (int, float)
            and math.isfinite(threshold) and 0.05 <= threshold <= 0.6):
        result.update(calibrated=True, near_threshold=threshold)
    for key in ('word_mode', 'sound', 'guide'):
        if type(data.get(key)) is bool:
            result[key] = data[key]
    return result

def load():
    try:
        return validate(json.loads(Path(PATH).read_text(encoding='utf-8')))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.exception('Cannot read settings; using defaults')
        return {}

def save(data):
    temporary = None
    try:
        path = Path(PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.settings-', suffix='.tmp', delete=False) as f:
            temporary = f.name
            json.dump(validate(data), f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        return True
    except (OSError, ValueError):
        log.exception('Cannot save settings')
        return False
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
