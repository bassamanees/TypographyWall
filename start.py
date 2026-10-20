"""Run with the Python 3.14 interpreter selected in PyCharm."""
import sys
from pathlib import Path
import subprocess

if sys.version_info[:2] != (3, 14):
    raise SystemExit("Select Python 3.14 in PyCharm, then run this file.")
root = Path(__file__).resolve().parent
raise SystemExit(subprocess.call([sys.executable, str(root / "main.py"), *sys.argv[1:]], cwd=root))
