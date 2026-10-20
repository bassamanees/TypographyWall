"""Rotating diagnostics, including uncaught worker exceptions."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
from .settings import DATA_DIR

def configure():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    try:
        folder = Path(DATA_DIR) / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / 'wall.log', maxBytes=2_000_000,
                                      backupCount=3, encoding='utf-8')
    except OSError:
        pass
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root.addHandler(handler)
    previous = sys.excepthook
    def uncaught(kind, value, tb):
        root.critical('Unhandled exception', exc_info=(kind, value, tb))
        previous(kind, value, tb)
    sys.excepthook = uncaught
    def worker(args):
        root.error('Unhandled thread exception', exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = worker
